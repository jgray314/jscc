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
        # Contacts land under the right FK for later-stage apps.
        if app.stage in {"recruiter_screen", "hm_screen", "technical_loop", "onsite", "closed"}:
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


def test_seed_reset_wipes_existing(conn: sqlite3.Connection) -> None:
    seed_synthetic(conn, now=FIXED_NOW, random_seed=1)
    first = len(list_applications(conn))
    # Re-seed with a different RNG seed; default reset=True must clear the prior fixture.
    seed_synthetic(conn, now=FIXED_NOW, random_seed=2)
    second = len(list_applications(conn))
    assert first == second == 25  # not doubled
