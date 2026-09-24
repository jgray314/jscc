"""Baseline JD fetcher (per D6). Requests-based fetch + readability
extraction for content isolation. Optional Playwright fallback (Slice B3b)
renders JS-heavy pages when the plain-requests fetch looks JS-required --
gated behind the `playwright_fallback` pipeline.yaml flag since it's a
heavy, opt-in dependency.

Every outcome is one of two shapes: a successful `FetchResult` with
extracted title/body text, or a failed one carrying a `FailureMode` for
the caller to route into a `DLQEntry`. This function never raises for
network- or content-shaped failures -- only for programmer error.

Requests leave here through `_get_guarded`, which enforces an http(s)
scheme allowlist, rejects hosts resolving to non-public addresses,
pins the actual connection to the addresses it just checked (closing a
DNS-rebinding gap a rating of "reasoned not exploited" once missed --
see `_pinned_resolution`), re-checks every redirect hop, ignores proxy
environment variables (see `_http_get`), and caps the body size.
Known residual: the Playwright fallback is handed an already-checked URL,
but the browser then follows its own redirects without those guards. It is
off by default and opt-in per config.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
import socket
import threading
from urllib.parse import urljoin, urlsplit

import requests
from pydantic import BaseModel
from readability import Document

from .models import FailureMode

DEFAULT_TIMEOUT_S = 10.0
_MIN_CONTENT_CHARS = 200

_BLOCKED_STATUS_CODES = {401, 403, 429, 451}
_PAYWALL_STATUS_CODES = {402}

# `fetch_jd` takes a URL and hands whatever comes back to an LLM, which makes
# it a fetch primitive: without guards, the cloud
# instance-metadata endpoint on the link-local range, `http://localhost:8080/`,
# `file:///etc/passwd`, or any public URL that 302s to one of those, is fetched
# and its contents forwarded. (Metadata and loopback addresses are described
# here rather than quoted -- their digit runs match the pre-commit scanner's
# phone pattern, the same false positive the model id in llm_client.py hits.)
# Practical risk is low for a CLI where the user types the URL -- but a repo
# whose pitch is structural safety should not leave an SSRF-shaped hole in the
# one function that touches the network.
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5
_CHUNK_BYTES = 64 * 1024


class _UrlRejected(Exception):
    """The URL (or a redirect hop) failed the pre-request guards."""


class _ResponseTooLarge(Exception):
    """The body exceeded `_MAX_RESPONSE_BYTES` and was abandoned mid-stream."""


def _resolve_host(host: str) -> list[str]:
    """Every address `host` resolves to. Separate function so tests can
    replace it -- the suite stays offline, and the guard stays testable
    against addresses no CI runner would actually route to."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _normalize_host(name: str | bytes) -> str:
    """The form of a hostname the connection layer will look up: lowercase, no
    trailing dot, IDNA (punycode) for a non-ASCII name. `requests`/`urllib3`
    convert an internationalized host to punycode before they resolve it, so a
    pin keyed on the URL's own spelling never matches their lookup. Both the
    URL check and the pin compare through this one function.
    """
    if isinstance(name, bytes):
        name = name.decode("ascii", "replace")
    name = name.rstrip(".").lower()
    return name.encode("idna").decode("ascii")


def _check_url(url: str) -> tuple[str, list[str]]:
    """Raise `_UrlRejected` unless `url` is a public http(s) destination.

    Checks the *resolved* addresses, not just the literal host: a hostname
    whose A record points into the link-local metadata range is the same
    attack as the address literal, and a scheme allowlist alone would wave
    it through.

    Returns `(host, addresses)` -- the caller pins the actual request to
    exactly these addresses (see `_pinned_resolution`). Checking here and
    letting `requests` re-resolve independently for the real connection is a
    DNS-rebinding gap: a short-TTL record can answer this lookup with a
    public address and the connection's own lookup, moments later, with the
    cloud instance-metadata address or any other private one (same false
    positive as the top-of-file comment; described here rather than quoted
    for the same scanner reason). An earlier review rated this gap
    "reasoned, not exploited"; a later one found the concrete rebinding path.
    The two resolutions were never pinned together until this.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise _UrlRejected(f"scheme {scheme or '(none)'!r} not allowed; http and https only")
    host = parts.hostname
    if not host:
        raise _UrlRejected("URL has no host")
    try:
        host = _normalize_host(host)
    except UnicodeError as e:
        raise _UrlRejected(f"host {host!r} is not a valid domain name: {e}") from e
    try:
        addresses = _resolve_host(host)
    except OSError as e:
        raise _UrlRejected(f"could not resolve {host}: {e}") from e
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise _UrlRejected(f"{host} resolves to non-public address {address}")
    return host, addresses


_PIN_LOCK = threading.Lock()


@contextlib.contextmanager
def _pinned_resolution(host: str, addresses: list[str]):
    """Force any DNS lookup for `host` to return exactly `addresses` for the
    duration of the block.

    `urllib3` (under `requests`) resolves the host itself via
    `socket.getaddrinfo` when it opens the connection -- a second, entirely
    independent lookup from the one `_check_url` already validated. Without
    this, a rebinding-capable DNS answer could pass `_check_url` and still
    hand the real connection a different, unchecked address. Patching the
    module-level `socket.getaddrinfo` (rather than something urllib3-specific)
    works regardless of urllib3's internal connection-pooling API and is
    restored in `finally` even if the request raises.
    """
    host = _normalize_host(host)

    def _is_pinned_host(node) -> bool:
        try:
            return _normalize_host(node) == host
        except (UnicodeError, AttributeError):
            return node == host

    def _pinned(node, port, family=0, type=0, proto=0, flags=0):
        if not _is_pinned_host(node):
            return real_getaddrinfo(node, port, family, type, proto, flags)
        results = []
        for address in addresses:
            is_v6 = ":" in address
            fam = socket.AF_INET6 if is_v6 else socket.AF_INET
            sockaddr = (address, port or 0, 0, 0) if is_v6 else (address, port or 0)
            results.append((fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return results

    # `socket.getaddrinfo` is process-global, so two overlapping pins would
    # each save the other's patched function as "real" and one restore would
    # leave the process permanently pinned. Serialize the block; a fetch is
    # seconds, and the CLI fetches one posting at a time.
    with _PIN_LOCK:
        real_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = _pinned
        try:
            yield
        finally:
            socket.getaddrinfo = real_getaddrinfo


def _http_get(url: str, **kwargs) -> requests.Response:
    """One GET with proxy environment variables ignored.

    With `HTTPS_PROXY` (or `ALL_PROXY`) set, `requests` connects to the proxy
    and the proxy resolves the target host itself: a third lookup that
    `_pinned_resolution` never sees, so a rebinding answer at that point
    reaches whatever the proxy can reach. `trust_env = False` closes that
    path. The cost is that a machine which can only reach the web through a
    proxy gets `blocked` fetches, which route to the DLQ's manual-paste
    remedy like any other blocked posting. The session is not closed here
    because a streamed response still needs its connection.
    """
    session = requests.Session()
    session.trust_env = False
    return session.get(url, **kwargs)


def _get_guarded(url: str, timeout: float) -> requests.Response:
    """GET `url`, re-running `_check_url` on every redirect hop.

    `allow_redirects=False` plus an explicit loop is the point: requests
    would otherwise follow a 302 into a private address without the guard
    ever seeing the second URL, which is the usual way this hole is reached.
    Each hop's request also runs inside `_pinned_resolution` so the
    connection can't resolve to anything other than what was just checked.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        host, addresses = _check_url(current)
        with _pinned_resolution(host, addresses):
            response = _http_get(current, timeout=timeout, allow_redirects=False, stream=True)
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        response.close()
        current = urljoin(current, location)
    raise _UrlRejected(f"more than {_MAX_REDIRECTS} redirects")


_CHARSET_RE = re.compile(rb"""charset["'\s=]+([a-zA-Z0-9_:.+-]+)""", re.I)
_META_SNIFF_BYTES = 4096


def _header_charset(content_type: str) -> str | None:
    """The charset the *server* declared, or None if it declared none.

    Deliberately parsed from the raw header rather than read off
    `response.encoding`: requests fills that field in with ISO-8859-1 for any
    `text/*` response with no charset parameter, following an HTTP/1.1 default
    that RFC 7231 removed. That default is indistinguishable, at the attribute,
    from a server that really did say ISO-8859-1 -- and treating "said nothing"
    as "said Latin-1" is how a UTF-8 page becomes mojibake.
    """
    _, _, params = content_type.partition(";")
    if "charset=" not in params.lower():
        return None
    match = _CHARSET_RE.search(params.encode("ascii", "ignore"))
    return match.group(1).decode("ascii") if match else None


def _decode_body(body: bytes, content_type: str) -> str:
    """Bytes to text, in the order the HTML standard actually specifies.

    1. The charset the server declared, if it declared one.
    2. A `<meta charset>` in the head, which is where a page that knows its own
       encoding but is served by a misconfigured host says so.
    3. UTF-8, strict -- the modern default, and strict so that failure is
       detectable rather than silently mangled.
    4. cp1252 with replacement, for genuinely legacy bytes. This is the last
       resort, not the first assumption.

    Known limit: a legacy page in a non-Latin encoding that declares nothing
    anywhere decodes wrong. Statistical detection would cover it, at the cost
    of a direct dependency on a charset-detection library; not worth it for
    job postings, and better to have the boundary written down than guessed at.
    """
    declared = _header_charset(content_type)
    if declared is None:
        meta = _CHARSET_RE.search(body[:_META_SNIFF_BYTES])
        declared = meta.group(1).decode("ascii", "ignore") if meta else None
    if declared:
        try:
            return body.decode(declared, errors="replace")
        except LookupError:
            pass  # a charset name Python doesn't know; fall through
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def _read_body(response: requests.Response) -> str:
    """Read the body with a hard size cap, then decode it.

    Streamed rather than taking `response.text`, because a cap that only
    checks Content-Length after buffering the whole body is not a cap --
    the header is optional and can lie.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge(
                f"response exceeded the {_MAX_RESPONSE_BYTES // (1024 * 1024)} MB cap"
            )
        chunks.append(chunk)
    return _decode_body(b"".join(chunks), response.headers.get("content-type", ""))


class FetchResult(BaseModel):
    ok: bool
    title: str = ""
    raw_text: str = ""
    failure_mode: FailureMode | None = None
    error_detail: str = ""
    used_playwright: bool = False


def _failure(mode: FailureMode, detail: str, *, used_playwright: bool = False) -> FetchResult:
    return FetchResult(
        ok=False, failure_mode=mode, error_detail=detail, used_playwright=used_playwright
    )


def _extract(html: str) -> tuple[str, str]:
    """Return (title, body_text) via readability. Body may be thin/empty.

    **Never raises.** Both readability and lxml throw on input they can't
    parse -- `lxml.html.fromstring("")` raises `ParserError: Document is
    empty`, and an empty-body `200` is a routine bot-block response, not an
    exotic case -- so letting that exception escape would break both this
    module's never-raises contract and the CLI's "produces an Application or a
    DLQEntry, never crashes".

    A parse failure is a content-shaped failure, so it degrades to empty
    text and lets the thin-content path route it: `extraction_failed` when
    the Playwright fallback is off, and a render retry when it's on -- which
    is the right call, since a server that returned an empty body to plain
    HTTP may well render fine in a browser.

    The catch is deliberately broad. Narrowing it to today's exception types
    would reintroduce this exact bug the next time lxml or readability
    raises something new from the same operation.
    """
    try:
        doc = Document(html)
        return doc.short_title(), _strip_tags(doc.summary())
    except Exception:
        return "", ""


def _looks_js_required(body_text: str) -> bool:
    """Thin extracted content is our proxy for 'this page needs JS to render'.

    A real SPA shell (empty <div id="root">, all content injected client-side)
    and a page readability just failed to parse both land here -- we can't
    tell them apart without rendering, so both route through the fallback
    the same way when it's enabled.
    """
    return len(body_text.strip()) < _MIN_CONTENT_CHARS


def fetch_jd(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    use_playwright_fallback: bool = False,
) -> FetchResult:
    try:
        response = _get_guarded(url, timeout)
    except _UrlRejected as e:
        return _failure(FailureMode.blocked, f"refused before fetching: {e}")
    except requests.Timeout as e:
        return _failure(FailureMode.timeout, str(e))
    except requests.RequestException as e:
        return _failure(FailureMode.blocked, str(e))

    try:
        if response.status_code in _PAYWALL_STATUS_CODES:
            return _failure(FailureMode.paywall, f"HTTP {response.status_code}")
        if 300 <= response.status_code < 400:
            # `_get_guarded` only follows hops requests itself recognizes as a
            # redirect (a Location header present). A 3xx with no Location, or
            # one requests doesn't call a redirect (304 Not Modified), reaches
            # here with no page to extract -- this used
            # to fall through the status checks below (none of which catch
            # anything under 400) and get treated as a successful, empty-ish
            # fetch.
            return _failure(
                FailureMode.blocked, f"HTTP {response.status_code} (unresolved redirect)"
            )
        if response.status_code in _BLOCKED_STATUS_CODES or response.status_code >= 400:
            return _failure(FailureMode.blocked, f"HTTP {response.status_code}")

        try:
            body = _read_body(response)
        except _ResponseTooLarge as e:
            # Content-shaped, so it routes to the DLQ's manual-paste remedy
            # like any other body we could not use.
            return _failure(FailureMode.extraction_failed, str(e))
        except requests.RequestException as e:
            return _failure(FailureMode.blocked, f"body read failed: {e}")
    finally:
        response.close()

    title, body_text = _extract(body)

    if not _looks_js_required(body_text):
        return FetchResult(ok=True, title=title, raw_text=body_text)

    if not use_playwright_fallback:
        return _failure(
            FailureMode.extraction_failed,
            f"readability extracted only {len(body_text.strip())} chars",
        )

    try:
        rendered_html = _render_with_playwright(url, timeout)
    except PlaywrightFetchError as e:
        return _failure(FailureMode.blocked, f"playwright render failed: {e}", used_playwright=True)

    title, body_text = _extract(rendered_html)
    if _looks_js_required(body_text):
        return _failure(
            FailureMode.extraction_failed,
            f"readability extracted only {len(body_text.strip())} chars after playwright render",
            used_playwright=True,
        )

    return FetchResult(ok=True, title=title, raw_text=body_text, used_playwright=True)


class PlaywrightFetchError(Exception):
    pass


def _render_with_playwright(url: str, timeout: float) -> str:
    """Render `url` in headless Chromium and return the final DOM's HTML.

    Imports playwright lazily so the base install doesn't pay for it, and
    the whole thing is a pure function of (url, timeout) -> html so it's
    easy to monkeypatch in tests without a real browser.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as e:
        raise PlaywrightFetchError(str(e)) from e


def _strip_tags(html: str) -> str:
    from lxml.html import fromstring

    tree = fromstring(html)
    return tree.text_content().strip()
