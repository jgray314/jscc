from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jscc.models import Resolution
from jscc.seed import build_seed, seed_synthetic
from jscc.storage import (
    connect,
    init_db,
    list_applications,
    list_contacts,
    list_dlq_entries,
    list_interactions,
)


FIXED_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "seed.db"
    c = connect(db_path)
    init_db(c)
    yield c
    c.close()


def test_build_seed_is_deterministic() -> None:
    a1, c1, i1, d1 = build_seed(now=FIXED_NOW, random_seed=42)
    a2, c2, i2, d2 = build_seed(now=FIXED_NOW, random_seed=42)
    assert [a.company for a in a1] == [a.company for a in a2]
    assert [a.stage for a in a1] == [a.stage for a in a2]
    assert [a.fit_score for a in a1] == [a.fit_score for a in a2]
    assert [i.type for i in i1] == [i.type for i in i2]
    assert [e.failure_mode for e in d1] == [e.failure_mode for e in d2]


def test_different_seeds_differ() -> None:
    a1, _, _, _ = build_seed(now=FIXED_NOW, random_seed=42)
    a2, _, _, _ = build_seed(now=FIXED_NOW, random_seed=1337)
    # Not literally different in every field, but the ordered company assignment must differ.
    assert [a.company for a in a1] != [a.company for a in a2]


def test_seed_counts_and_distribution() -> None:
    apps, contacts, interactions, dlq = build_seed(now=FIXED_NOW)
    assert len(apps) == 25
    assert len(dlq) == 3
    stages = {a.stage for a in apps}
    assert stages == {
        "identified",
        "applied",
        "recruiter_screen",
        "hm_screen",
        "technical_loop",
        "onsite",
        "closed",
    }
    # Later-stage apps carry contacts; identified apps do not.
    apps_by_id = {a.id: a for a in apps}
    for c in contacts:
        assert apps_by_id[c.application_id].stage != "identified"
    # DLQ entries created without an application FK (mirrors the ingest failure path).
    for e in dlq:
        assert e.application_id is None


def test_seed_produces_fresh_and_stale_mix() -> None:
    apps, *_ = build_seed(now=FIXED_NOW)
    ages_days = [
        (FIXED_NOW - a.last_interaction_at).days for a in apps if a.last_interaction_at
    ]
    assert min(ages_days) <= 3   # at least one fresh
    assert max(ages_days) >= 20  # at least one stale under any reasonable threshold


def test_seed_synthetic_e2e_roundtrips_through_storage(conn: sqlite3.Connection) -> None:
    counts = seed_synthetic(conn, now=FIXED_NOW)
    assert counts["applications"] == 25
    assert counts["dlq_entries"] == 3

    loaded_apps = list_applications(conn)
    assert len(loaded_apps) == 25

    for app in loaded_apps:
        # Non-closed later-stage apps carry contacts. Closed apps may close at any
        # depth, including before a recruiter reply, so contact presence is not
        # guaranteed for closed.
        if app.stage in {"recruiter_screen", "hm_screen", "technical_loop", "onsite"}:
            assert list_contacts(conn, app.id), f"expected contacts for stage {app.stage}"
        # Every non-identified app has at least one interaction (the applied event).
        if app.stage != "identified":
            assert list_interactions(conn, app.id), f"expected interactions for {app.stage}"

    # DLQ: one resolved (manual_paste), two still unresolved.
    unresolved = list_dlq_entries(conn)
    all_entries = list_dlq_entries(conn, unresolved_only=False)
    assert len(unresolved) == 2
    assert len(all_entries) == 3
    resolutions = {e.resolution for e in all_entries}
    assert Resolution.manual_paste in resolutions
    assert Resolution.unresolved in resolutions


def test_interactions_are_chronologically_ordered(conn: sqlite3.Connection) -> None:
    """Each application's interaction chain must be non-decreasing in occurred_at."""
    seed_synthetic(conn, now=FIXED_NOW)
    for app in list_applications(conn):
        chain = list_interactions(conn, app.id)
        for prev, curr in zip(chain, chain[1:]):
            assert prev.occurred_at <= curr.occurred_at, (
                f"chain out of order for {app.company}: {prev.type} @ {prev.occurred_at} "
                f"followed by {curr.type} @ {curr.occurred_at}"
            )


def test_hm_contact_referenced_by_screen_or_onsite(conn: sqlite3.Connection) -> None:
    """When an HM contact exists on an app, at least one interaction must link to it."""
    seed_synthetic(conn, now=FIXED_NOW)
    for app in list_applications(conn):
        contacts = list_contacts(conn, app.id)
        hm_ids = {c.id for c in contacts if c.role.value == "hm"}
        if not hm_ids:
            continue
        chain = list_interactions(conn, app.id)
        linked = [i for i in chain if i.contact_id in hm_ids]
        assert linked, (
            f"HM contact on {app.company} but no interaction references it"
        )


def test_extracted_jd_responsibilities_vary_across_apps() -> None:
    """The responsibility pool must produce more than one distinct phrase across the fixture."""
    apps, *_ = build_seed(now=FIXED_NOW)
    seen: set[str] = set()
    for a in apps:
        if a.extracted_jd:
            responsibilities = a.extracted_jd.get("responsibilities", [])  # type: ignore[union-attr]
            seen.update(responsibilities)
    assert len(seen) >= 5, f"responsibilities look too uniform: {seen}"


def test_last_interaction_at_matches_chain_end(conn: sqlite3.Connection) -> None:
    """Application.last_interaction_at is the timestamp of the final logged interaction."""
    seed_synthetic(conn, now=FIXED_NOW)
    for app in list_applications(conn):
        chain = list_interactions(conn, app.id)
        if not chain:
            assert app.last_interaction_at is None
            continue
        final = chain[-1].occurred_at
        assert app.last_interaction_at == final, (
            f"last_interaction_at drifted from chain end for {app.company}"
        )


def test_seed_reset_wipes_existing(conn: sqlite3.Connection) -> None:
    seed_synthetic(conn, now=FIXED_NOW, random_seed=1)
    first = len(list_applications(conn))
    # Re-seed with a different RNG seed; default reset=True must clear the prior fixture.
    seed_synthetic(conn, now=FIXED_NOW, random_seed=2)
    second = len(list_applications(conn))
    assert first == second == 25  # not doubled
