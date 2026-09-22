"""Dashboard smoke tests (E1: boots and reads the active mode's DB; E2a:
pipeline/funnel/stale-alert rendering).

Mirrors the CliRunner pattern in test_cli.py -- isolate JSCC_DATA, point at a
tmp_path data dir, seed known data, and assert on the response instead of
inspecting internals. FastAPI's TestClient drives the app in-process (no
socket), so this stays as fast as any other unit test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jscc.mode import ENV_VAR, Mode
from jscc.models import Application, FetchStatus
from jscc.seed import seed_synthetic
from jscc.storage import create_application, list_applications, open_for_mode
from jscc.web import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"

FIXED_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def synthetic_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        seed_synthetic(conn, random_seed=1)
    finally:
        conn.close()
    return tmp_path


def _insert_app(data_dir: Path, *, title: str, company: str, stage: str, days_ago: int) -> None:
    """Insert one application at a known age relative to FIXED_NOW, so E2a
    tests can assert exact stale/fresh outcomes against the real stages.yaml
    thresholds, the same hand-built-fixture approach test_report.py uses.
    """
    when = FIXED_NOW - timedelta(days=days_ago)
    conn = open_for_mode(Mode.synthetic, data_dir)
    try:
        create_application(
            conn,
            Application(
                title=title,
                company=company,
                stage=stage,
                fetch_status=FetchStatus.ok,
                created_at=when,
                updated_at=when,
                last_interaction_at=when,
            ),
        )
    finally:
        conn.close()


# ---- E1: boot + mode -----------------------------------------------------------


def test_index_boots_and_shows_seeded_row_count(synthetic_data_dir: Path) -> None:
    app = create_app(data_dir=synthetic_data_dir)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    conn = open_for_mode(Mode.synthetic, synthetic_data_dir)
    try:
        expected_count = len(list_applications(conn))
    finally:
        conn.close()
    assert expected_count > 0
    assert f"{expected_count} application" in response.text
    assert "Mode: <strong>synthetic</strong>" in response.text


def test_index_shows_synthetic_mode_banner(synthetic_data_dir: Path) -> None:
    app = create_app(data_dir=synthetic_data_dir)
    client = TestClient(app)

    response = client.get("/")

    assert "SYNTHETIC MODE" in response.text


def test_index_real_mode_hides_synthetic_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, "real")
    conn = open_for_mode(Mode.real, tmp_path)
    conn.close()

    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "SYNTHETIC MODE" not in response.text
    assert "Mode: <strong>real</strong>" in response.text


# ---- E2a: funnel, pipeline, stale alerts ----------------------------------------


@pytest.fixture
def two_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh applied-stage app (well under its 14-day threshold) and a
    stale onsite-stage app (well over its 10-day threshold), against the
    real config/stages.yaml this dashboard actually loads.
    """
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    conn.close()
    _insert_app(tmp_path, title="Fresh Role", company="Acme", stage="applied", days_ago=2)
    _insert_app(tmp_path, title="Stale Role", company="Zeta", stage="onsite", days_ago=25)
    return tmp_path


def _get(client: TestClient) -> object:
    return client.get("/", params={"now": FIXED_NOW.isoformat()})


def test_funnel_counts_all_configured_stages(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    response = _get(TestClient(app))

    assert response.status_code == 200
    # Every configured stage appears, including the five with zero apps.
    for stage in [
        "identified",
        "applied",
        "recruiter_screen",
        "hm_screen",
        "technical_loop",
        "onsite",
        "offer",
        "closed",
    ]:
        assert f"<td>{stage}</td>" in response.text
    assert "<strong>Total</strong></td><td><strong>2</strong>" in response.text


def test_pipeline_lists_applications_under_their_stage(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    response = _get(TestClient(app))

    assert "Acme — Fresh Role" in response.text
    assert "Zeta — Stale Role" in response.text


def test_stale_alerts_flags_only_the_overdue_app(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    response = _get(TestClient(app))

    assert "Stale alerts (1)" in response.text
    assert "Zeta" in response.text
    assert "overdue by 15d" in response.text  # 25 days - 10-day onsite threshold
    assert "Fresh Role" not in response.text.split("Stale alerts")[1]


def test_now_query_param_rejects_naive_timestamp(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.get("/", params={"now": "2026-08-28T12:00:00"})

    assert response.status_code == 400
    assert "timezone offset" in response.text


def test_now_query_param_rejects_malformed_timestamp(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.get("/", params={"now": "not-a-date"})

    assert response.status_code == 400
