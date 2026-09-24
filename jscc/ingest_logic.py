"""Shared extract-then-store logic for every path that turns raw JD text into
an Application: `ingest` (URL and `--paste`), `resolve-dlq`, and the
dashboard's DLQ resolve form (Slice E2b).

Split out of `jscc/cli/ingest.py` (Slice E2b) so the web layer can reuse it
without importing a CLI module -- `jscc/dlq.py` sits between this module and
both callers, and `jscc/cli/ingest.py` imports from here rather than the
reverse, so there is exactly one place this logic lives.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

from .extraction import extract_jd
from .llm_client import StubExtractionClient, default_client
from .mode import Mode
from .models import Application, FetchStatus
from .storage import (
    create_application,
    get_application,
    list_applications,
    update_application,
)

# A DLQ entry needs a source_url (NOT NULL), and a pasted JD has none. The
# sentinel keeps the paste path's failures visible in `dlq list` rather than
# silently unrecoverable; `resolve-dlq` recognises it and skips URL-derived
# company inference. Same "(pasted)" spelling the company default already uses.
PASTED_SOURCE = "(pasted)"

# Every newly-ingested Application starts here, regardless of path (URL,
# paste, or DLQ resolution). Single source of truth -- `stages.yaml` can
# reorder or rename downstream stages freely without this changing.
FIRST_STAGE = "identified"


STUB_IN_REAL_MODE_MESSAGE = (
    "no ANTHROPIC_API_KEY is configured, so extraction would use the placeholder "
    "client and save a placeholder application into the real database. Set the key, "
    "or switch to synthetic mode (unset JSCC_DATA)."
)


def stub_extractor_in_real_mode(mode: Mode) -> bool:
    """Whether extracting now would write placeholder output into real data.

    Without a key, extraction falls back to a stub that returns fixed text. In
    synthetic mode that is what the demo and the tests want; in real mode it
    would store a made-up application beside real ones, silently.
    """
    return mode is Mode.real and isinstance(default_client(), StubExtractionClient)


def company_from_url(url: str) -> str:
    """Fallback company name for when extraction doesn't find one -- an
    ATS page whose JD text never names the employer, or the stub client.
    `extract_and_create_application` prefers `ExtractedJD.company` over
    this whenever extraction returns one."""
    netloc = urlparse(url).netloc
    return netloc.removeprefix("www.") or url


def find_duplicate_application(
    conn, *, source_url: str | None, raw_text: str
) -> Application | None:
    """`ingest` had no duplicate detection of any kind --
    the same JD file, or the same URL, ingested three times produced three
    Applications, feeding straight into `funnel_counts`/`detect_stale` just
    like duplicate DLQ resolutions did one command over. A URL is matched by exact
    equality; pasted text has no URL to key on, so it's matched by exact
    `source_raw` equality among the other paste-sourced applications.
    Decided: refuse re-ingesting silently. `ingest` notifies the caller and
    either confirms interactively or requires `--update`, then reprocesses
    into the *existing* row rather than creating a second one."""
    apps = list_applications(conn)
    if source_url is not None:
        return next((a for a in apps if a.source_url == source_url), None)
    return next((a for a in apps if a.source_url is None and a.source_raw == raw_text), None)


def extract_and_create_application(
    conn: sqlite3.Connection,
    *,
    raw_text: str,
    source_url: str | None,
    company_override: str | None,
    fallback_company: str,
    fallback_title: str | None,
    fetch_status: FetchStatus = FetchStatus.ok,
    update_id: str | None = None,
) -> tuple[str, Application]:
    """Shared extract-then-store path for `ingest` (URL and `--paste`) and
    `resolve-dlq` -- the DoD for Slice B4 requires the paste path produce the
    same Application shape as the URL path, so both funnel through here.

    Company precedence, highest first: `company_override` (the user typed
    `--company` explicitly -- that's a deliberate correction and wins over
    anything inferred), then `ExtractedJD.company` (extraction found a name
    in the JD text itself), then `fallback_company` (a URL-domain guess or
    "(pasted)", used only when extraction comes back null).

    `fetch_status` defaults to `ok` for the URL path, which really did fetch
    cleanly; callers on the paste and DLQ-resolution paths pass the status
    that actually describes how the raw text arrived.

    `update_id`, when given (the confirmed-reprocess path), overwrites that
    existing Application's fields instead of creating a new one -- `stage`
    is deliberately left untouched, since a reprocess is a correction to the
    extracted record, not a reset of pipeline progress.
    """
    extracted = extract_jd(raw_text, conn=conn)
    fields = dict(
        source_url=source_url,
        source_raw=raw_text,
        fetch_status=fetch_status,
        title=extracted.title or fallback_title or "(untitled)",
        company=company_override or extracted.company or fallback_company,
        # The whole extraction, not just the field the title comes from: D9
        # splits extract from score because the intermediate output has
        # independent product value, and caching it needs it stored.
        extracted_jd=extracted.model_dump(),
    )
    if update_id is not None:
        update_application(conn, update_id, **fields)
        return update_id, get_application(conn, update_id)
    app = Application(**fields, stage=FIRST_STAGE)
    app_id = create_application(conn, app)
    return app_id, app
