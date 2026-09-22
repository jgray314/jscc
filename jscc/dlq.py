"""DLQ resolution (D6's escape hatch), shared by the CLI's `resolve-dlq`
command and the dashboard's resolve form (Slice E2b).

One path, not two: a second reimplementation of this logic in the web layer
would risk drifting from the CLI's idempotency guard (gate finding M-1/M-9)
or its `dlq_*` fetch-status mapping (gate finding M-6). `resolve_dlq_entry_via_paste`
returns a typed result rather than echoing or exiting, so each caller renders
it in its own idiom (click exit codes for the CLI, an HTML result page here).
Both still call `extract_and_create_application`, which reaches the model
only through `extract_jd` -> `stage_call.call_stage` -- the single choke
point `test_llm_egress.py` enforces -- so this module adds no second path to
the LLM itself, only to the surrounding bookkeeping.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

import anthropic

from .extraction import ExtractionParseError
from .ingest_logic import PASTED_SOURCE, company_from_url, extract_and_create_application
from .llm_client import UnknownModelPricingError
from .models import FailureMode, FetchStatus, Resolution
from .storage import list_dlq_entries
from .storage import resolve_dlq_entry as _store_resolution

# Every newly-created dlq_* status names the failure the original fetch hit
# ("this record was recovered from a paywall", not "this record was fetched
# cleanly"). FailureMode.other has no corresponding dlq_* status -- it's
# defined but never actually produced by fetcher.py today, so there is
# nothing live to map it to; `.get(..., FetchStatus.manual)` covers it
# defensively without inventing a status for a code path that doesn't run.
_DLQ_RESOLVED_FETCH_STATUS: dict[FailureMode, FetchStatus] = {
    FailureMode.paywall: FetchStatus.dlq_paywall,
    FailureMode.blocked: FetchStatus.dlq_blocked,
    FailureMode.timeout: FetchStatus.dlq_timeout,
    FailureMode.extraction_failed: FetchStatus.dlq_extraction_failed,
}


class DLQResolveOutcome(StrEnum):
    created = "created"
    already_resolved = "already_resolved"
    extraction_failed = "extraction_failed"
    llm_api_error = "llm_api_error"
    config_error = "config_error"
    not_found = "not_found"


@dataclass
class DLQResolveResult:
    outcome: DLQResolveOutcome
    entry_id: str
    application_id: str | None = None
    detail: str | None = None
    prior_resolution: Resolution | None = None


def resolve_dlq_entry_via_paste(
    conn: sqlite3.Connection,
    entry_id: str,
    paste_text: str,
    *,
    company: str | None = None,
) -> DLQResolveResult:
    """Resolve one DLQ entry by pasting the JD text manually. Creates the
    Application the original fetch couldn't, then marks the entry resolved.
    Idempotent: an already-resolved entry is reported, not re-processed --
    re-running this against the same entry must never create a second
    Application (gate finding M-1).
    """
    entries = list_dlq_entries(conn, unresolved_only=False)
    entry = next((e for e in entries if e.id == entry_id), None)
    if entry is None:
        return DLQResolveResult(outcome=DLQResolveOutcome.not_found, entry_id=entry_id)

    if entry.resolution is not Resolution.unresolved:
        return DLQResolveResult(
            outcome=DLQResolveOutcome.already_resolved,
            entry_id=entry_id,
            prior_resolution=entry.resolution,
        )

    fallback_company_val = (
        "(pasted)" if entry.source_url == PASTED_SOURCE else company_from_url(entry.source_url)
    )
    try:
        app_id, _app = extract_and_create_application(
            conn,
            raw_text=paste_text,
            source_url=None if entry.source_url == PASTED_SOURCE else entry.source_url,
            company_override=company,
            fallback_company=fallback_company_val,
            fallback_title=None,
            fetch_status=_DLQ_RESOLVED_FETCH_STATUS.get(entry.failure_mode, FetchStatus.manual),
        )
    except ExtractionParseError as e:
        # No new DLQ entry here -- one already exists and stays unresolved,
        # which is the correct record. Creating a second would duplicate the
        # queue on every retry.
        return DLQResolveResult(
            outcome=DLQResolveOutcome.extraction_failed, entry_id=entry_id, detail=str(e)
        )
    except anthropic.APIError as e:
        return DLQResolveResult(
            outcome=DLQResolveOutcome.llm_api_error, entry_id=entry_id, detail=str(e)
        )
    except UnknownModelPricingError as e:
        return DLQResolveResult(
            outcome=DLQResolveOutcome.config_error, entry_id=entry_id, detail=str(e)
        )

    _store_resolution(conn, entry_id, Resolution.manual_paste, application_id=app_id)
    return DLQResolveResult(
        outcome=DLQResolveOutcome.created, entry_id=entry_id, application_id=app_id
    )
