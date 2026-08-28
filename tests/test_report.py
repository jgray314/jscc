from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jscc.config import StagesConfig
from jscc.models import Application, FetchStatus
from jscc.report import (
    StaleAlert,
    detect_stale,
    format_report,
    funnel_counts,
)
from jscc.seed import seed_synthetic
from jscc.storage import connect, init_db, list_applications


FIXED_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def stages_cfg() -> StagesConfig:
    return StagesConfig(
        stages=["identified", "applied", "onsite", "closed"],
        staleness_thresholds_days={
            "identified": 7,
            "applied": 14,
            "onsite": 10,
            "closed": 999,
        },
    )


def _app(*, stage: str, days_ago: int, title: str = "T", company: str = "C") -> Application:
    when = FIXED_NOW - timedelta(days=days_ago)
    return Application(
        title=title,
        company=company,
        stage=stage,
        fetch_status=FetchStatus.ok,
        created_at=when,
        updated_at=when,
        last_interaction_at=when,
    )


def test_funnel_counts_includes_zero_stages(stages_cfg: StagesConfig) -> None:
    apps = [_app(stage="applied", days_ago=1), _app(stage="applied", days_ago=2)]
    counts = funnel_counts(apps, stages_cfg)
    assert counts == {"identified": 0, "applied": 2, "onsite": 0, "closed": 0}


def test_funnel_counts_flags_unknown_stage(stages_cfg: StagesConfig) -> None:
    apps = [_app(stage="applied", days_ago=1), _app(stage="mystery", days_ago=1)]
    counts = funnel_counts(apps, stages_cfg)
    assert counts["applied"] == 1
    assert counts["__unknown__"] == 1


def test_detect_stale_flags_over_threshold(stages_cfg: StagesConfig) -> None:
    apps = [
        _app(stage="applied", days_ago=20, company="Late"),   # over 14d threshold
        _app(stage="applied", days_ago=5, company="Fresh"),   # under threshold
    ]
    alerts = detect_stale(apps, stages_cfg, now=FIXED_NOW)
    assert [a.company for a in alerts] == ["Late"]
    assert alerts[0].overdue_by_days == 6


def test_detect_stale_at_threshold_is_stale(stages_cfg: StagesConfig) -> None:
    apps = [_app(stage="applied", days_ago=14, company="Edge")]
    alerts = detect_stale(apps, stages_cfg, now=FIXED_NOW)
    assert [a.company for a in alerts] == ["Edge"]


def test_detect_stale_sorts_most_overdue_first(stages_cfg: StagesConfig) -> None:
    apps = [
        _app(stage="applied", days_ago=16, company="Small"),  # overdue by 2
        _app(stage="applied", days_ago=40, company="Big"),    # overdue by 26
        _app(stage="applied", days_ago=22, company="Mid"),    # overdue by 8
    ]
    alerts = detect_stale(apps, stages_cfg, now=FIXED_NOW)
    assert [a.company for a in alerts] == ["Big", "Mid", "Small"]


def test_detect_stale_uses_created_at_when_no_last_interaction(
    stages_cfg: StagesConfig,
) -> None:
    old_created = FIXED_NOW - timedelta(days=20)
    app = Application(
        title="Old identified",
        company="ID Co",
        stage="identified",
        fetch_status=FetchStatus.ok,
        created_at=old_created,
        updated_at=old_created,
        last_interaction_at=None,
    )
    alerts = detect_stale([app], stages_cfg, now=FIXED_NOW)
    assert [a.company for a in alerts] == ["ID Co"]
    assert alerts[0].days_since_last_interaction == 20


def test_detect_stale_skips_unknown_stage(stages_cfg: StagesConfig) -> None:
    apps = [_app(stage="mystery", days_ago=999, company="X")]
    assert detect_stale(apps, stages_cfg, now=FIXED_NOW) == []


def test_detect_stale_respects_high_threshold_for_closed(stages_cfg: StagesConfig) -> None:
    apps = [_app(stage="closed", days_ago=500, company="OldClosed")]
    alerts = detect_stale(apps, stages_cfg, now=FIXED_NOW)
    assert alerts == []


def test_detect_stale_handles_naive_last_interaction(stages_cfg: StagesConfig) -> None:
    naive = (FIXED_NOW - timedelta(days=30)).replace(tzinfo=None)
    app = Application(
        title="Naive Late",
        company="Naive Co",
        stage="applied",
        fetch_status=FetchStatus.ok,
        created_at=naive,
        updated_at=naive,
        last_interaction_at=naive,
    )
    alerts = detect_stale([app], stages_cfg, now=FIXED_NOW)
    assert [a.company for a in alerts] == ["Naive Co"]
    assert alerts[0].days_since_last_interaction == 30


def test_format_report_renders_funnel_and_stale(stages_cfg: StagesConfig) -> None:
    counts = funnel_counts(
        [_app(stage="applied", days_ago=20), _app(stage="onsite", days_ago=1)],
        stages_cfg,
    )
    alerts = [
        StaleAlert(
            application_id="a1",
            title="Staff SWE",
            company="Late Corp",
            stage="applied",
            days_since_last_interaction=25,
            threshold_days=14,
        )
    ]
    text = format_report(counts, alerts, stages_cfg)
    assert "Funnel" in text
    assert "applied" in text
    assert "Stale alerts (1)" in text
    assert "Late Corp" in text
    assert "overdue by 11d" in text


def test_format_report_none_when_no_alerts(stages_cfg: StagesConfig) -> None:
    counts = funnel_counts([], stages_cfg)
    text = format_report(counts, [], stages_cfg)
    assert "(none)" in text
    assert "(total)" in text


def test_report_e2e_against_seed(tmp_path: Path) -> None:
    """Seed a DB, run funnel + detect_stale against a pinned `now`, verify structure."""
    db_path = tmp_path / "e2e.db"
    conn = connect(db_path)
    init_db(conn)
    seed_synthetic(conn, now=FIXED_NOW)
    apps = list_applications(conn)
    conn.close()

    stages_cfg = StagesConfig(
        stages=[
            "identified", "applied", "recruiter_screen", "hm_screen",
            "technical_loop", "onsite", "offer", "closed",
        ],
        staleness_thresholds_days={
            "identified": 7, "applied": 14, "recruiter_screen": 7,
            "hm_screen": 7, "technical_loop": 10, "onsite": 10,
            "offer": 30, "closed": 999,
        },
    )

    counts = funnel_counts(apps, stages_cfg)
    assert counts["applied"] == 8
    assert counts["identified"] == 6
    assert counts["closed"] == 2
    assert sum(counts.values()) == 25

    alerts = detect_stale(apps, stages_cfg, now=FIXED_NOW)
    # Fixture spans 0-120 days ago across stages; must produce SOME alerts and
    # exclude closed apps.
    assert alerts, "expected at least one stale alert against the seeded fixture"
    assert all(a.stage != "closed" for a in alerts)
    # Most-overdue-first ordering:
    for prev, curr in zip(alerts, alerts[1:]):
        assert prev.overdue_by_days >= curr.overdue_by_days
