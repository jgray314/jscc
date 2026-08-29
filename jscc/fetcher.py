"""Baseline JD fetcher (per D6). Requests-based fetch + readability
extraction for content isolation. No JS rendering here -- Playwright
fallback for JS-heavy sites is Slice B3b, gated behind a config flag.

Every outcome is one of two shapes: a successful `FetchResult` with
extracted title/body text, or a failed one carrying a `FailureMode` for
the caller to route into a `DLQEntry`. This function never raises for
network- or content-shaped failures -- only for programmer error.
"""
from __future__ import annotations

import requests
from pydantic import BaseModel
from readability import Document

from .models import FailureMode

DEFAULT_TIMEOUT_S = 10.0
_MIN_CONTENT_CHARS = 200

_BLOCKED_STATUS_CODES = {401, 403, 429, 451}
_PAYWALL_STATUS_CODES = {402}


class FetchResult(BaseModel):
    ok: bool
    title: str = ""
    raw_text: str = ""
    failure_mode: FailureMode | None = None
    error_detail: str = ""


def _failure(mode: FailureMode, detail: str) -> FetchResult:
    return FetchResult(ok=False, failure_mode=mode, error_detail=detail)


def fetch_jd(url: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> FetchResult:
    try:
        response = requests.get(url, timeout=timeout)
    except requests.Timeout as e:
        return _failure(FailureMode.timeout, str(e))
    except requests.RequestException as e:
        return _failure(FailureMode.blocked, str(e))

    if response.status_code in _PAYWALL_STATUS_CODES:
        return _failure(FailureMode.paywall, f"HTTP {response.status_code}")
    if response.status_code in _BLOCKED_STATUS_CODES or response.status_code >= 500:
        return _failure(FailureMode.blocked, f"HTTP {response.status_code}")
    if response.status_code >= 400:
        return _failure(FailureMode.blocked, f"HTTP {response.status_code}")

    doc = Document(response.text)
    title = doc.short_title()
    body_html = doc.summary()
    body_text = _strip_tags(body_html)

    if len(body_text.strip()) < _MIN_CONTENT_CHARS:
        return _failure(
            FailureMode.extraction_failed,
            f"readability extracted only {len(body_text.strip())} chars",
        )

    return FetchResult(ok=True, title=title, raw_text=body_text)


def _strip_tags(html: str) -> str:
    from lxml.html import fromstring

    tree = fromstring(html)
    return tree.text_content().strip()
