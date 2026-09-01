"""Baseline JD fetcher (per D6). Requests-based fetch + readability
extraction for content isolation. Optional Playwright fallback (Slice B3b)
renders JS-heavy pages when the plain-requests fetch looks JS-required --
gated behind the `playwright_fallback` pipeline.yaml flag since it's a
heavy, opt-in dependency.

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
    used_playwright: bool = False


def _failure(mode: FailureMode, detail: str, *, used_playwright: bool = False) -> FetchResult:
    return FetchResult(
        ok=False, failure_mode=mode, error_detail=detail, used_playwright=used_playwright
    )


def _extract(html: str) -> tuple[str, str]:
    """Return (title, body_text) via readability. Body may be thin/empty."""
    doc = Document(html)
    title = doc.short_title()
    body_text = _strip_tags(doc.summary())
    return title, body_text


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

    title, body_text = _extract(response.text)

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
