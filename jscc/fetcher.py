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
re-checks every redirect hop, and caps the body size (gate finding M5).
Known residual: the Playwright fallback is handed an already-checked URL,
but the browser then follows its own redirects without those guards. It is
off by default and opt-in per config.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests
from pydantic import BaseModel
from readability import Document

from .models import FailureMode

DEFAULT_TIMEOUT_S = 10.0
_MIN_CONTENT_CHARS = 200

_BLOCKED_STATUS_CODES = {401, 403, 429, 451}
_PAYWALL_STATUS_CODES = {402}

# Gate finding M5. `fetch_jd` takes a URL and hands whatever comes back to an
# LLM, which makes it a fetch primitive: without guards, the cloud
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


def _check_url(url: str) -> None:
    """Raise `_UrlRejected` unless `url` is a public http(s) destination.

    Checks the *resolved* addresses, not just the literal host: a hostname
    whose A record points into the link-local metadata range is the same
    attack as the address literal, and a scheme allowlist alone would wave
    it through.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise _UrlRejected(f"scheme {scheme or '(none)'!r} not allowed; http and https only")
    host = parts.hostname
    if not host:
        raise _UrlRejected("URL has no host")
    try:
        addresses = _resolve_host(host)
    except OSError as e:
        raise _UrlRejected(f"could not resolve {host}: {e}") from e
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise _UrlRejected(f"{host} resolves to non-public address {address}")


def _get_guarded(url: str, timeout: float) -> requests.Response:
    """GET `url`, re-running `_check_url` on every redirect hop.

    `allow_redirects=False` plus an explicit loop is the point: requests
    would otherwise follow a 302 into a private address without the guard
    ever seeing the second URL, which is the usual way this hole is reached.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        _check_url(current)
        response = requests.get(current, timeout=timeout, allow_redirects=False, stream=True)
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        response.close()
        current = urljoin(current, location)
    raise _UrlRejected(f"more than {_MAX_REDIRECTS} redirects")


def _read_body(response: requests.Response) -> str:
    """Read the body with a hard size cap.

    Streamed rather than taking `response.text`, because a cap that only
    checks Content-Length after buffering the whole body is not a cap --
    the header is optional and can lie.

    Decoding is deliberately simple: the declared charset, else UTF-8, with
    undecodable bytes replaced. `response.text` would fall back to charset
    sniffing here; for a document that is about to be reduced to plain text
    and handed to a model, replacement characters are a better failure than
    a guessed codec.
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
    return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")


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
    exotic case. Before the B5 hardening slice that exception escaped
    `fetch_jd` and crashed `python -m jscc ingest` with exit 1 and no DLQ
    entry, breaking both this module's never-raises contract and B3a's DoD
    ("produces Application OR DLQEntry, never crashes"). Gate finding H1.

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
        if response.status_code in _BLOCKED_STATUS_CODES or response.status_code >= 500:
            return _failure(FailureMode.blocked, f"HTTP {response.status_code}")
        if response.status_code >= 400:
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
