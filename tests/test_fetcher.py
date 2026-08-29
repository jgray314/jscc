from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from jscc.fetcher import fetch_jd
from jscc.models import FailureMode

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
    resp = Mock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    resp.raise_for_status = Mock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
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
