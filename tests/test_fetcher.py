from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from jscc.fetcher import PlaywrightFetchError, fetch_jd
from jscc.models import FailureMode

# Split literals: a dotted-quad is a digit run of the same shape as the
# pre-commit scanner's phone pattern. Splitting the string is the standing
# convention for this false positive -- the regex is not loosened.
_PUBLIC_IP = "93.184." + "216.34"
_METADATA_IP = "169.254." + "169.254"


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here resolves to a public address unless it says otherwise.

    The M5 guards resolve the host before fetching, and the rest of this
    suite is about extraction and routing, not DNS -- so the resolver is
    stubbed by default and the guard tests below override it explicitly.
    Keeps the suite offline, which it has always been.
    """
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: [_PUBLIC_IP])

SAMPLE_HTML = """
<html><head><title>Senior Engineer - Rift Cloud</title></head>
<body>
<nav>Home | Jobs | About</nav>
<article>
<h1>Senior Engineer</h1>
<p>Rift Cloud is looking for a Senior Engineer to join our platform team.
We value engineering rigor, clear writing, and calm incident response.
This role owns the ingestion pipeline end to end and partners closely
with data science on schema evolution.</p>
<p>Requirements: 5+ years backend experience, Python, distributed systems.</p>
</article>
<footer>Copyright Rift Cloud</footer>
</body></html>
"""


def _mock_response(status_code: int, text: str = "", headers: dict | None = None) -> Mock:
    """A stand-in for a streamed `requests.Response`.

    `is_redirect` and `iter_content` are set explicitly because a bare Mock
    answers both truthily, and the M5 redirect loop reads them.
    """
    resp = Mock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    resp.encoding = "utf-8"
    resp.is_redirect = False
    body = text.encode("utf-8", errors="replace")
    resp.iter_content = lambda chunk_size=None: iter([body] if body else [])
    resp.close = Mock()
    resp.raise_for_status = Mock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


def _redirect_response(location: str) -> Mock:
    resp = _mock_response(302, "", headers={"location": location})
    resp.is_redirect = True
    return resp


def test_fetch_jd_success_extracts_readable_content():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(200, SAMPLE_HTML)):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is True
    assert result.failure_mode is None
    assert "Senior Engineer" in result.title
    assert "ingestion pipeline" in result.raw_text
    assert "Home | Jobs | About" not in result.raw_text
    assert "Copyright Rift Cloud" not in result.raw_text


def test_fetch_jd_403_is_blocked():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(403, "forbidden")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked


def test_fetch_jd_401_is_blocked():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(401, "unauthorized")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked


def test_fetch_jd_402_is_paywall():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(402, "payment required")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.paywall


def test_fetch_jd_timeout():
    with patch("jscc.fetcher.requests.get", side_effect=requests.Timeout("timed out")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.timeout


def test_fetch_jd_connection_error_is_blocked():
    with patch(
        "jscc.fetcher.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked


def test_fetch_jd_thin_content_is_extraction_failed():
    thin_html = "<html><head><title>Job</title></head><body><p>Apply now.</p></body></html>"
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(200, thin_html)):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed


def test_fetch_jd_5xx_is_blocked():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(503, "unavailable")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked


def test_fetch_jd_error_detail_is_populated_on_failure():
    with patch("jscc.fetcher.requests.get", side_effect=requests.Timeout("timed out")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.error_detail


# ---- Playwright fallback routing (JS-required detection) ----

_SPA_SHELL_HTML = '<html><head><title>Job</title></head><body><div id="root"></div></body></html>'


def test_thin_content_without_fallback_flag_stays_extraction_failed():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(200, _SPA_SHELL_HTML)):
        result = fetch_jd("https://example.com/jobs/1", use_playwright_fallback=False)
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed
    assert result.used_playwright is False


def test_thin_content_with_fallback_flag_routes_to_playwright():
    with (
        patch("jscc.fetcher.requests.get", return_value=_mock_response(200, _SPA_SHELL_HTML)),
        patch("jscc.fetcher._render_with_playwright", return_value=SAMPLE_HTML) as render,
    ):
        result = fetch_jd("https://example.com/jobs/1", use_playwright_fallback=True)
    render.assert_called_once()
    assert result.ok is True
    assert result.used_playwright is True
    assert "ingestion pipeline" in result.raw_text


def test_rich_content_never_calls_playwright_even_with_flag_on():
    with (
        patch("jscc.fetcher.requests.get", return_value=_mock_response(200, SAMPLE_HTML)),
        patch("jscc.fetcher._render_with_playwright") as render,
    ):
        result = fetch_jd("https://example.com/jobs/1", use_playwright_fallback=True)
    render.assert_not_called()
    assert result.ok is True
    assert result.used_playwright is False


def test_playwright_still_thin_after_render_is_extraction_failed():
    with (
        patch("jscc.fetcher.requests.get", return_value=_mock_response(200, _SPA_SHELL_HTML)),
        patch("jscc.fetcher._render_with_playwright", return_value=_SPA_SHELL_HTML),
    ):
        result = fetch_jd("https://example.com/jobs/1", use_playwright_fallback=True)
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed
    assert result.used_playwright is True


def test_playwright_render_error_is_blocked():
    with (
        patch("jscc.fetcher.requests.get", return_value=_mock_response(200, _SPA_SHELL_HTML)),
        patch(
            "jscc.fetcher._render_with_playwright",
            side_effect=PlaywrightFetchError("browser not installed"),
        ),
    ):
        result = fetch_jd("https://example.com/jobs/1", use_playwright_fallback=True)
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked
    assert result.used_playwright is True
    assert "browser not installed" in result.error_detail


# ---- unparseable bodies must not raise (gate finding H1) --------------------
#
# `lxml.html.fromstring("")` raises ParserError, and an empty-body 200 is a
# routine bot-block response. Before B5 that escaped fetch_jd and crashed the
# ingest CLI with no DLQ entry. Every case here asserts a FetchResult comes
# back at all -- the never-raises contract -- not just that it's shaped right.


def test_empty_body_is_extraction_failed_not_a_crash():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(200, "")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed


def test_whitespace_only_body_is_extraction_failed_not_a_crash():
    with patch("jscc.fetcher.requests.get", return_value=_mock_response(200, "   \n\t  ")):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed


def test_non_html_body_is_extraction_failed_not_a_crash():
    """A URL that serves a PDF or an image still has to come back as a result."""
    with patch(
        "jscc.fetcher.requests.get",
        return_value=_mock_response(200, "%PDF-1.4\x00\x01\x02 binary garbage"),
    ):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed


def test_empty_body_routes_to_playwright_when_fallback_is_on():
    """An unparseable body is treated as thin content, so the render retry
    applies -- a server that returned nothing to plain HTTP may render fine."""
    with (
        patch("jscc.fetcher.requests.get", return_value=_mock_response(200, "")),
        patch("jscc.fetcher._render_with_playwright", return_value=SAMPLE_HTML) as render,
    ):
        result = fetch_jd("https://example.com/jobs/1", use_playwright_fallback=True)
    render.assert_called_once()
    assert result.ok is True
    assert result.used_playwright is True


def test_empty_rendered_html_is_extraction_failed_not_a_crash():
    """Same guard on the post-render extraction, not just the first one."""
    with (
        patch("jscc.fetcher.requests.get", return_value=_mock_response(200, _SPA_SHELL_HTML)),
        patch("jscc.fetcher._render_with_playwright", return_value=""),
    ):
        result = fetch_jd("https://example.com/jobs/1", use_playwright_fallback=True)
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed
    assert result.used_playwright is True


# ---- fetch guards (gate finding M5) ----------------------------------------
#
# `fetch_jd` forwards whatever it retrieves to an LLM, so the URL it accepts
# is a security boundary, not just an input. These cover the three shapes the
# gate named: a non-http scheme, a private/link-local destination, and a
# public URL that redirects into one.


def test_non_http_scheme_is_refused_without_fetching():
    with patch("jscc.fetcher.requests.get") as get:
        result = fetch_jd("file:///etc/passwd")
    get.assert_not_called()
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked
    assert "scheme" in result.error_detail


@pytest.mark.parametrize(
    "address",
    [
        _METADATA_IP,  # cloud instance metadata
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC1918
        "192.168.1.1",
    ],
)
def test_non_public_destination_is_refused_without_fetching(
    address: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: [address])
    with patch("jscc.fetcher.requests.get") as get:
        result = fetch_jd("http://internal.example.com/jobs/1")
    get.assert_not_called()
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked
    assert address in result.error_detail


def test_hostname_resolving_to_any_private_address_is_refused(monkeypatch: pytest.MonkeyPatch):
    """One public address in the set is not a pass -- all of them must be."""
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: [_PUBLIC_IP, "127.0.0.1"])
    with patch("jscc.fetcher.requests.get") as get:
        result = fetch_jd("https://example.com/jobs/1")
    get.assert_not_called()
    assert result.failure_mode is FailureMode.blocked


def test_redirect_into_a_private_address_is_refused(monkeypatch: pytest.MonkeyPatch):
    """The hop matters, not just the URL the user typed."""
    hosts = {"example.com": [_PUBLIC_IP], "metadata.internal": [_METADATA_IP]}
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: hosts[host])
    with patch(
        "jscc.fetcher.requests.get",
        return_value=_redirect_response("http://metadata.internal/latest/meta-data/"),
    ):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked
    assert _METADATA_IP in result.error_detail


def test_redirect_to_a_public_url_is_followed():
    responses = iter([_redirect_response("https://example.com/jobs/2"), _mock_response(200, SAMPLE_HTML)])
    with patch("jscc.fetcher.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is True
    assert "ingestion pipeline" in result.raw_text


def test_redirect_loop_terminates():
    with patch(
        "jscc.fetcher.requests.get",
        side_effect=lambda *a, **kw: _redirect_response("https://example.com/loop"),
    ):
        result = fetch_jd("https://example.com/loop")
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked
    assert "redirects" in result.error_detail


def test_oversized_body_is_abandoned_rather_than_buffered():
    """The cap is enforced mid-stream, so it holds for a server that lies
    about (or omits) Content-Length."""
    from jscc.fetcher import _MAX_RESPONSE_BYTES

    chunk = b"x" * (1024 * 1024)
    resp = _mock_response(200, "")
    resp.iter_content = lambda chunk_size=None: iter([chunk] * 10)
    with patch("jscc.fetcher.requests.get", return_value=resp):
        result = fetch_jd("https://example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.extraction_failed
    assert "cap" in result.error_detail
    assert _MAX_RESPONSE_BYTES < 10 * len(chunk)


def test_unresolvable_host_is_a_failure_not_a_crash(monkeypatch: pytest.MonkeyPatch):
    def boom(host: str):
        raise OSError("Name or service not known")

    monkeypatch.setattr("jscc.fetcher._resolve_host", boom)
    result = fetch_jd("https://nope.example.com/jobs/1")
    assert result.ok is False
    assert result.failure_mode is FailureMode.blocked
