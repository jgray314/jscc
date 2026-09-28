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
import threading
from dataclasses import dataclass, field
from enum import StrEnum

import anthropic

from .extraction import ExtractionParseError
from .ingest_logic import (
    PASTED_SOURCE,
    STUB_IN_REAL_MODE_MESSAGE,
    ExtractOutcome,
    company_from_url,
    extract_and_create_application,
    find_duplicate_application,
)
from .llm_client import StubExtractionClient, UnknownModelPricingError, default_client
from .models import ExtractedJD, FailureMode, FetchStatus, Resolution
from .storage import delete_application, list_dlq_entries
from .storage import resolve_dlq_entry as _store_resolution

# Every newly-created dlq_* status names the failure the original fetch hit
# ("this record was recovered from a paywall", not "this record was fetched
# cleanly"). FailureMode.other has no dlq_* status because it is not a fetch
# failure: `ingest` records it when the *extraction* call hit an LLM API error
# (rate limit, overload), so the text is being supplied by paste at resolve time
# and `manual` describes it correctly. `.get(..., FetchStatus.manual)` is that
# mapping, not a defensive default.
_DLQ_RESOLVED_FETCH_STATUS: dict[FailureMode, FetchStatus] = {
    FailureMode.paywall: FetchStatus.dlq_paywall,
    FailureMode.blocked: FetchStatus.dlq_blocked,
    FailureMode.timeout: FetchStatus.dlq_timeout,
    FailureMode.extraction_failed: FetchStatus.dlq_extraction_failed,
}


class DLQResolveOutcome(StrEnum):
    created = "created"
    needs_confirmation = "needs_confirmation"
    already_resolved = "already_resolved"
    duplicate = "duplicate"
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
    # Set only on needs_confirmation -- see extract_and_create_application.
    extracted: ExtractedJD | None = None
    unverified_fields: frozenset[str] = field(default_factory=frozenset)


# One lock per entry, held from the "still unresolved?" read to the write that
# marks it resolved. The LLM call in between takes seconds, so without it a
# double-click (or two tabs) has both requests pass the read and each create an
# Application. It covers every caller in this process (the dashboard's request
# threads, the CLI); a second *process* is caught by the compare-and-set below.
_ENTRY_LOCKS: dict[str, threading.Lock] = {}
_ENTRY_LOCKS_GUARD = threading.Lock()


def _entry_lock(entry_id: str) -> threading.Lock:
    with _ENTRY_LOCKS_GUARD:
        return _ENTRY_LOCKS.setdefault(entry_id, threading.Lock())


# What extraction actually returned, held server-side between a
# needs_confirmation response and a caller's confirm-and-resubmit, keyed by
# entry_id. This is the trust boundary a client-supplied extraction blob
# cannot be: `resolve_dlq_entry_via_paste`'s `use_pending_extraction` looks
# here, not at anything the caller sends over the wire, so a resubmit can
# only replay what THIS server actually extracted for THIS entry -- a
# crafted request with a fabricated extraction has nothing to pop and falls
# through to a real (and therefore re-verified) extraction attempt instead.
# Popped on use, so a resubmit only ever consumes one pending extraction
# once; an abandoned confirm screen leaves a stray entry until the process
# restarts, the same acceptable-for-a-short-lived-personal-tool tradeoff
# `_ENTRY_LOCKS` already makes by never shrinking either.
_PENDING_EXTRACTIONS: dict[str, ExtractedJD] = {}
_PENDING_EXTRACTIONS_GUARD = threading.Lock()


def _store_pending_extraction(entry_id: str, extracted: ExtractedJD) -> None:
    with _PENDING_EXTRACTIONS_GUARD:
        _PENDING_EXTRACTIONS[entry_id] = extracted


def _pop_pending_extraction(entry_id: str) -> ExtractedJD | None:
    with _PENDING_EXTRACTIONS_GUARD:
        return _PENDING_EXTRACTIONS.pop(entry_id, None)


def resolve_dlq_entry_via_paste(
    conn: sqlite3.Connection,
    entry_id: str,
    paste_text: str,
    *,
    company: str | None = None,
    title: str | None = None,
    refuse_stub: bool = False,
    extracted_override: ExtractedJD | None = None,
    use_pending_extraction: bool = False,
) -> DLQResolveResult:
    """Resolve one DLQ entry by pasting the JD text manually. Creates the
    Application the original fetch couldn't, then marks the entry resolved.
    Idempotent: an already-resolved entry is reported, not re-processed --
    re-running this against the same entry must never create a second
    Application (gate finding M-1). With `refuse_stub`, a run that would use the
    placeholder extractor is refused before anything is written (real mode). Concurrent callers are serialized per
    entry, and the final write is a compare-and-set, so the guarantee holds
    while the model call is in flight, not only between sequential runs.

    If extraction's title/company doesn't verify against `paste_text` and
    neither `title` nor `company` covers it, nothing is created: the result
    is `needs_confirmation`, and the extraction is held server-side, keyed
    by `entry_id` (`_PENDING_EXTRACTIONS`) -- not just returned to the
    caller -- because the caller here can be an untrusted HTTP request.
    `extracted_override` (a trusted, same-process replay -- the CLI's
    interactive confirm, which never crosses a network hop) and
    `use_pending_extraction` (an untrusted caller's confirm-and-resubmit,
    which looks up this server's own record instead of trusting anything
    the request sent) are two different trust levels, not two spellings of
    the same thing; see `extract_and_create_application` for where
    `extracted_override` is actually consumed.
    """
    with _entry_lock(entry_id):
        return _resolve_locked(
            conn,
            entry_id,
            paste_text,
            company=company,
            title=title,
            refuse_stub=refuse_stub,
            extracted_override=extracted_override,
            use_pending_extraction=use_pending_extraction,
        )


def _resolve_locked(
    conn: sqlite3.Connection,
    entry_id: str,
    paste_text: str,
    *,
    company: str | None,
    title: str | None,
    refuse_stub: bool,
    extracted_override: ExtractedJD | None,
    use_pending_extraction: bool = False,
) -> DLQResolveResult:
    if use_pending_extraction and extracted_override is None:
        extracted_override = _pop_pending_extraction(entry_id)
        # No pending extraction (spoofed request, expired process, or the
        # entry moved on) is not an error: falling through to a real
        # extraction attempt re-runs `unverified_fields` on it like any
        # other call, which is the safe behavior -- never skip verification
        # on the strength of an untrusted "trust me, I confirmed" flag
        # alone.

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

    if refuse_stub and isinstance(default_client(), StubExtractionClient):
        return DLQResolveResult(
            outcome=DLQResolveOutcome.config_error,
            entry_id=entry_id,
            detail=STUB_IN_REAL_MODE_MESSAGE,
        )

    # The same posting may already be an Application: ingested successfully
    # after this entry was queued, or created by an earlier resolve that
    # crashed before it marked the entry. Creating another would count it twice.
    existing = find_duplicate_application(
        conn,
        source_url=None if entry.source_url == PASTED_SOURCE else entry.source_url,
        raw_text=paste_text,
    )
    if existing is not None:
        _store_resolution(
            conn,
            entry_id,
            Resolution.wont_fix,
            application_id=existing.id,
            only_if_unresolved=True,
        )
        return DLQResolveResult(
            outcome=DLQResolveOutcome.duplicate, entry_id=entry_id, application_id=existing.id
        )

    fallback_company_val = (
        "(pasted)" if entry.source_url == PASTED_SOURCE else company_from_url(entry.source_url)
    )
    try:
        extract_result = extract_and_create_application(
            conn,
            raw_text=paste_text,
            source_url=None if entry.source_url == PASTED_SOURCE else entry.source_url,
            company_override=company,
            title_override=title,
            fallback_company=fallback_company_val,
            fallback_title=None,
            fetch_status=_DLQ_RESOLVED_FETCH_STATUS.get(entry.failure_mode, FetchStatus.manual),
            extracted_override=extracted_override,
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

    if extract_result.outcome is ExtractOutcome.needs_confirmation:
        # Nothing was created -- extraction ran, but its title or company
        # didn't verify against the pasted text. The entry stays unresolved;
        # a caller re-runs with `title`/`company` and either `extracted_override`
        # (trusted, same-process) or `use_pending_extraction=True` (untrusted,
        # looks up what's stored here) once a human has confirmed or corrected it.
        _store_pending_extraction(entry_id, extract_result.extracted)
        return DLQResolveResult(
            outcome=DLQResolveOutcome.needs_confirmation,
            entry_id=entry_id,
            extracted=extract_result.extracted,
            unverified_fields=extract_result.unverified_fields,
        )
    app_id = extract_result.application_id

    if not _store_resolution(
        conn, entry_id, Resolution.manual_paste, application_id=app_id, only_if_unresolved=True
    ):
        # Another process resolved the entry while the model call ran. Its
        # Application is the one the entry links to; undo ours so the funnel
        # does not count the posting twice.
        delete_application(conn, app_id)
        current = next(
            (e for e in list_dlq_entries(conn, unresolved_only=False) if e.id == entry_id), None
        )
        return DLQResolveResult(
            outcome=DLQResolveOutcome.already_resolved,
            entry_id=entry_id,
            prior_resolution=current.resolution if current else None,
        )
    return DLQResolveResult(
        outcome=DLQResolveOutcome.created, entry_id=entry_id, application_id=app_id
    )
