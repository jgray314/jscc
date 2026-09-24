"""Direct tests for `resolve_dlq_entry_via_paste` (jscc/dlq.py), independent
of the CLI's click plumbing -- test_cli.py already exercises this
behavior end to end through `resolve-dlq`; this module is what the
dashboard's resolve form (Slice E2b) calls directly, so it gets its own
focused coverage the way `report.py`'s pure functions do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jscc.dlq import DLQResolveOutcome, resolve_dlq_entry_via_paste
from jscc.mode import ENV_VAR, Mode
from jscc.models import DLQEntry, FailureMode, FetchStatus, Resolution
from jscc.storage import (
    create_application,
    create_dlq_entry,
    list_applications,
    list_dlq_entries,
    open_for_mode,
)


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    connection = open_for_mode(Mode.synthetic, tmp_path)
    yield connection
    connection.close()


def test_not_found(conn) -> None:
    result = resolve_dlq_entry_via_paste(conn, "nonexistent-id", "some jd text")
    assert result.outcome is DLQResolveOutcome.not_found
    assert list_applications(conn) == []


def test_creates_application_and_carries_forward_the_dlq_fetch_status(conn) -> None:
    entry_id = create_dlq_entry(
        conn,
        DLQEntry(
            source_url="https://example.com/jobs/5",
            failure_mode=FailureMode.blocked,
            error_detail="HTTP 403",
        ),
    )

    result = resolve_dlq_entry_via_paste(conn, entry_id, "Senior Engineer at Rift Cloud. " * 20)

    assert result.outcome is DLQResolveOutcome.created
    apps = list_applications(conn)
    assert len(apps) == 1
    assert apps[0].id == result.application_id
    assert apps[0].source_url == "https://example.com/jobs/5"
    # Gate finding M-6: the entry's original failure mode carries forward as
    # the matching dlq_* status, not a bare "ok" as if it had fetched cleanly.
    assert apps[0].fetch_status == FetchStatus.dlq_blocked
    resolved = next(e for e in list_dlq_entries(conn, unresolved_only=False) if e.id == entry_id)
    assert resolved.resolution == Resolution.manual_paste
    assert resolved.application_id == apps[0].id


def test_company_override_takes_precedence(conn) -> None:
    entry_id = create_dlq_entry(
        conn,
        DLQEntry(
            source_url="https://example.com/jobs/9",
            failure_mode=FailureMode.blocked,
            error_detail="HTTP 403",
        ),
    )

    resolve_dlq_entry_via_paste(
        conn, entry_id, "Senior Engineer at Rift Cloud. " * 20, company="Corrected Co"
    )

    assert list_applications(conn)[0].company == "Corrected Co"


def test_is_idempotent(conn) -> None:
    entry_id = create_dlq_entry(
        conn,
        DLQEntry(
            source_url="https://example.com/jobs/6",
            failure_mode=FailureMode.timeout,
            error_detail="timed out",
        ),
    )

    first = resolve_dlq_entry_via_paste(conn, entry_id, "Senior Engineer at Rift Cloud. " * 20)
    second = resolve_dlq_entry_via_paste(conn, entry_id, "a different paste entirely")

    assert first.outcome is DLQResolveOutcome.created
    assert second.outcome is DLQResolveOutcome.already_resolved
    assert second.prior_resolution == Resolution.manual_paste
    assert len(list_applications(conn)) == 1


def test_pasted_source_sentinel_is_not_treated_as_a_real_url(conn) -> None:
    """A DLQ entry from a failed extraction on an `ingest --paste` call has
    no real URL -- `PASTED_SOURCE` covers the NOT NULL column. The resolved
    Application must not carry that sentinel forward as its source_url."""
    entry_id = create_dlq_entry(
        conn,
        DLQEntry(
            source_url="(pasted)",
            failure_mode=FailureMode.extraction_failed,
            error_detail="bad JSON",
        ),
    )

    result = resolve_dlq_entry_via_paste(conn, entry_id, "a jd")

    assert result.outcome is DLQResolveOutcome.created
    assert list_applications(conn)[0].source_url is None


def test_a_lost_race_undoes_its_application_and_reports_already_resolved(
    conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second process resolves the entry while this one is inside its model
    call (the per-entry lock only covers this process). The compare-and-set on
    the final write loses, and the Application this call created is removed, so
    the funnel counts the posting once, under the winner's Application."""
    from jscc import dlq
    from jscc.models import Application
    from jscc.storage import resolve_dlq_entry

    entry_id = create_dlq_entry(
        conn, DLQEntry(source_url="https://example.com/jobs/7", failure_mode=FailureMode.blocked)
    )
    real = dlq.extract_and_create_application

    def raced(*args, **kwargs):
        result = real(*args, **kwargs)
        other = open_for_mode(Mode.synthetic, tmp_path)
        try:
            winner = Application(company="Winner", title="Engineer", stage="lead")
            create_application(other, winner)
            resolve_dlq_entry(other, entry_id, Resolution.manual_paste, application_id=winner.id)
        finally:
            other.close()
        return result

    monkeypatch.setattr(dlq, "extract_and_create_application", raced)

    result = resolve_dlq_entry_via_paste(conn, entry_id, "Senior Engineer at Rift Cloud. " * 20)

    assert result.outcome is DLQResolveOutcome.already_resolved
    assert [a.company for a in list_applications(conn)] == ["Winner"]
    entry = next(e for e in list_dlq_entries(conn, unresolved_only=False) if e.id == entry_id)
    assert entry.application_id == list_applications(conn)[0].id


def test_a_posting_that_is_already_an_application_is_not_created_again(conn) -> None:
    """The URL was ingested successfully after the entry was queued, or an earlier
    resolve crashed after creating the Application but before marking the entry.
    Either way a second Application would count the posting twice."""
    from jscc.models import Application
    from jscc.storage import create_application

    existing = Application(
        company="Rift Cloud",
        title="Engineer",
        stage="identified",
        source_url="https://example.com/jobs/11",
    )
    create_application(conn, existing)
    entry_id = create_dlq_entry(
        conn, DLQEntry(source_url="https://example.com/jobs/11", failure_mode=FailureMode.blocked)
    )

    result = resolve_dlq_entry_via_paste(conn, entry_id, "Senior Engineer at Rift Cloud. " * 20)

    assert result.outcome is DLQResolveOutcome.duplicate
    assert result.application_id == existing.id
    assert [a.id for a in list_applications(conn)] == [existing.id]
    entry = next(e for e in list_dlq_entries(conn, unresolved_only=False) if e.id == entry_id)
    assert entry.resolution is Resolution.wont_fix
    assert entry.application_id == existing.id


def test_a_repeated_paste_is_recognized_as_a_duplicate(conn) -> None:
    from jscc.ingest_logic import PASTED_SOURCE

    text = "Senior Engineer at Rift Cloud. " * 20
    first = create_dlq_entry(
        conn, DLQEntry(source_url=PASTED_SOURCE, failure_mode=FailureMode.extraction_failed)
    )
    second = create_dlq_entry(
        conn, DLQEntry(source_url=PASTED_SOURCE, failure_mode=FailureMode.extraction_failed)
    )
    assert resolve_dlq_entry_via_paste(conn, first, text).outcome is DLQResolveOutcome.created
    assert resolve_dlq_entry_via_paste(conn, second, text).outcome is DLQResolveOutcome.duplicate
    assert len(list_applications(conn)) == 1
