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
from jscc.models import (
    Application,
    Contact,
    ContactRole,
    DLQEntry,
    FailureMode,
    FetchStatus,
    Interaction,
    InteractionType,
)
from jscc.seed import seed_synthetic
from jscc.storage import (
    create_application,
    create_contact,
    create_dlq_entry,
    create_interaction,
    list_applications,
    open_for_mode,
)
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


# ---- E2b: application detail, DLQ list, DLQ resolve -----------------------------


@pytest.fixture
def app_with_contacts_and_interactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        app_id = create_application(
            conn,
            Application(
                title="Staff Engineer",
                company="Acme Robotics",
                stage="applied",
                fetch_status=FetchStatus.ok,
                fit_score=82.5,
                fit_rationale="Strong stack match.",
                extracted_jd={"level": "staff", "location": "Remote"},
            ),
        )
        create_contact(
            conn,
            Contact(application_id=app_id, name="Jordan Lee", role=ContactRole.recruiter),
        )
        create_interaction(
            conn,
            Interaction(
                application_id=app_id,
                type=InteractionType.applied,
                occurred_at=FIXED_NOW,
                notes="Submitted via careers page.",
            ),
        )
    finally:
        conn.close()
    return tmp_path, app_id


def test_application_detail_shows_scored_fields_contacts_and_interactions(
    app_with_contacts_and_interactions: tuple[Path, str],
) -> None:
    data_dir, app_id = app_with_contacts_and_interactions
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.get(f"/applications/{app_id}")

    assert response.status_code == 200
    assert "Staff Engineer" in response.text
    assert "Acme Robotics" in response.text
    assert "82.5" in response.text
    assert "Strong stack match." in response.text
    assert "staff" in response.text
    assert "Jordan Lee" in response.text
    assert "Submitted via careers page." in response.text


def test_application_detail_404s_on_unknown_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    conn.close()
    app = create_app(data_dir=tmp_path, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.get("/applications/nonexistent-id")

    assert response.status_code == 404


def test_pipeline_click_through_reaches_detail_page(
    app_with_contacts_and_interactions: tuple[Path, str],
) -> None:
    data_dir, app_id = app_with_contacts_and_interactions
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    index_response = client.get("/")
    assert f'href="/applications/{app_id}"' in index_response.text

    detail_response = client.get(f"/applications/{app_id}")
    assert detail_response.status_code == 200


@pytest.fixture
def dlq_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        entry_id = create_dlq_entry(
            conn,
            DLQEntry(
                source_url="https://example.com/jobs/5",
                failure_mode=FailureMode.blocked,
                error_detail="HTTP 403",
            ),
        )
    finally:
        conn.close()
    return tmp_path, entry_id


def test_dlq_list_shows_unresolved_entries(dlq_data_dir: tuple[Path, str]) -> None:
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.get("/dlq")

    assert response.status_code == 200
    assert "blocked" in response.text
    assert "https://example.com/jobs/5" in response.text
    assert f'href="/dlq/{entry_id}/resolve"' in response.text


def test_dlq_list_empty_by_default_hides_nothing_to_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    conn.close()
    app = create_app(data_dir=tmp_path, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.get("/dlq")

    assert response.status_code == 200
    assert "no unresolved entries" in response.text


def test_dlq_resolve_form_renders_for_unresolved_entry(dlq_data_dir: tuple[Path, str]) -> None:
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.get(f"/dlq/{entry_id}/resolve")

    assert response.status_code == 200
    assert "<textarea" in response.text
    assert f'action="/dlq/{entry_id}/resolve"' in response.text


def test_dlq_resolve_post_creates_application_and_shows_success(
    dlq_data_dir: tuple[Path, str],
) -> None:
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.post(
        f"/dlq/{entry_id}/resolve",
        data={"paste_text": "Senior Engineer at Rift Cloud. " * 20, "company": ""},
    )

    assert response.status_code == 200
    assert "Created application" in response.text
    conn = open_for_mode(Mode.synthetic, data_dir)
    try:
        apps = list_applications(conn)
    finally:
        conn.close()
    assert len(apps) == 1
    assert apps[0].fetch_status == FetchStatus.dlq_blocked
    assert f'href="/applications/{apps[0].id}"' in response.text


def test_dlq_resolve_post_is_idempotent(dlq_data_dir: tuple[Path, str]) -> None:
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    first = client.post(
        f"/dlq/{entry_id}/resolve",
        data={"paste_text": "Senior Engineer at Rift Cloud. " * 20, "company": ""},
    )
    second = client.post(
        f"/dlq/{entry_id}/resolve",
        data={"paste_text": "a different paste entirely", "company": ""},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "already resolved" in second.text
    conn = open_for_mode(Mode.synthetic, data_dir)
    try:
        apps = list_applications(conn)
    finally:
        conn.close()
    assert len(apps) == 1


def test_dlq_resolve_post_rejects_empty_paste_text(dlq_data_dir: tuple[Path, str]) -> None:
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app)

    response = client.post(f"/dlq/{entry_id}/resolve", data={"paste_text": "   ", "company": ""})

    assert response.status_code == 400
