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


def _client(app, **kwargs) -> TestClient:
    """A client that names the loopback host the way a browser at
    `http://localhost:8000` does; the app refuses any other Host (gate L1-1)."""
    return TestClient(app, base_url="http://localhost", **kwargs)


@pytest.fixture(autouse=True)
def _no_live_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dashboard's resolve form reaches an extraction client; with a real
    key in the environment it would make a billed call from the test suite."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


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
    client = _client(app)

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
    client = _client(app)

    response = client.get("/")

    assert "SYNTHETIC MODE" in response.text


def test_index_real_mode_hides_synthetic_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, "real")
    conn = open_for_mode(Mode.real, tmp_path)
    conn.close()

    app = create_app(data_dir=tmp_path)
    client = _client(app)

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
    response = _get(_client(app))

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
    response = _get(_client(app))

    assert "Acme — Fresh Role" in response.text
    assert "Zeta — Stale Role" in response.text


def test_stale_alerts_flags_only_the_overdue_app(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    response = _get(_client(app))

    assert "Stale alerts (1)" in response.text
    alerts_section = response.text.split("Stale alerts")[1]
    # "Zeta" also appears in the pipeline table above, so check the alerts section itself.
    assert "Zeta" in alerts_section
    assert "overdue by 15d" in alerts_section  # 25 days - 10-day onsite threshold
    assert "Fresh Role" not in alerts_section


def test_now_query_param_rejects_naive_timestamp(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    client = _client(app)

    response = client.get("/", params={"now": "2026-08-28T12:00:00"})

    assert response.status_code == 400
    assert "timezone offset" in response.text


def test_now_query_param_rejects_malformed_timestamp(two_app_data_dir: Path) -> None:
    app = create_app(data_dir=two_app_data_dir, config_dir=CONFIG_DIR)
    client = _client(app)

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
    client = _client(app)

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
    client = _client(app)

    response = client.get("/applications/nonexistent-id")

    assert response.status_code == 404


def test_pipeline_click_through_reaches_detail_page(
    app_with_contacts_and_interactions: tuple[Path, str],
) -> None:
    data_dir, app_id = app_with_contacts_and_interactions
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = _client(app)

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
    client = _client(app)

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
    client = _client(app)

    response = client.get("/dlq")

    assert response.status_code == 200
    assert "no unresolved entries" in response.text


def test_dlq_resolve_form_renders_for_unresolved_entry(dlq_data_dir: tuple[Path, str]) -> None:
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = _client(app)

    response = client.get(f"/dlq/{entry_id}/resolve")

    assert response.status_code == 200
    assert "<textarea" in response.text
    assert f'action="/dlq/{entry_id}/resolve"' in response.text


def test_dlq_resolve_post_creates_application_and_shows_success(
    dlq_data_dir: tuple[Path, str],
) -> None:
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = _client(app)

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
    client = _client(app)

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
    client = _client(app)

    response = client.post(f"/dlq/{entry_id}/resolve", data={"paste_text": "   ", "company": ""})

    assert response.status_code == 400


# ---- Request guards (Phase E gate L1-1, L1-2, L1-6, L1-7, L1-9) ---------------
#
# The server binds to loopback, which keeps other machines out but not a web
# page in the user's own browser: DNS rebinding makes the browser treat the
# dashboard as that page's own site. The guards refuse the two things such a
# page needs: a request that names a foreign Host, and a cross-origin POST.

_PASTE = {"paste_text": "Senior Engineer at Rift Cloud. " * 20, "company": ""}


def _apps(data_dir: Path) -> list[Application]:
    conn = open_for_mode(Mode.synthetic, data_dir)
    try:
        return list_applications(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/", "/dlq"])
def test_a_foreign_host_is_refused_and_leaks_nothing(
    dlq_data_dir: tuple[Path, str], path: str
) -> None:
    """The DNS-rebinding shape: the request reaches 127.0.0.1 but names the
    attacker's domain in Host."""
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    client = TestClient(app, base_url="http://attacker.example")

    response = client.get(path)

    assert response.status_code == 400
    assert entry_id not in response.text


@pytest.mark.parametrize(
    "base_url",
    ["http://localhost", "http://localhost:8000", "http://127.0.0.1:8000"],
)
def test_loopback_hosts_are_allowed(dlq_data_dir: tuple[Path, str], base_url: str) -> None:
    data_dir, _ = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)
    assert TestClient(app, base_url=base_url).get("/dlq").status_code == 200


def test_an_explicitly_allowed_host_is_accepted(dlq_data_dir: tuple[Path, str]) -> None:
    data_dir, _ = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR, allowed_hosts=["192.0.2.10"])
    assert TestClient(app, base_url="http://192.0.2.10").get("/dlq").status_code == 200
    assert TestClient(app, base_url="http://localhost").get("/dlq").status_code == 400


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://localhost:9999"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_a_cross_origin_resolve_post_is_refused(
    dlq_data_dir: tuple[Path, str], headers: dict[str, str]
) -> None:
    data_dir, entry_id = dlq_data_dir
    client = _client(create_app(data_dir=data_dir, config_dir=CONFIG_DIR))

    response = client.post(f"/dlq/{entry_id}/resolve", data=_PASTE, headers=headers)

    assert response.status_code == 403
    assert _apps(data_dir) == []


def test_a_same_origin_resolve_post_still_works(dlq_data_dir: tuple[Path, str]) -> None:
    data_dir, entry_id = dlq_data_dir
    client = _client(create_app(data_dir=data_dir, config_dir=CONFIG_DIR))

    response = client.post(
        f"/dlq/{entry_id}/resolve",
        data=_PASTE,
        headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 200
    assert len(_apps(data_dir)) == 1


def test_serve_allows_the_host_it_was_told_to_bind(
    synthetic_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from jscc.cli import cli

    captured: dict = {}
    monkeypatch.setattr("jscc.cli.web.uvicorn.run", lambda app, **kw: captured.update(app=app))
    result = CliRunner().invoke(
        cli,
        ["serve", "--data-dir", str(synthetic_data_dir), "--host", "192.0.2.10"],
    )
    assert result.exit_code == 0, result.output
    app = captured["app"]
    assert TestClient(app, base_url="http://192.0.2.10").get("/").status_code == 200
    assert TestClient(app, base_url="http://attacker.example").get("/").status_code == 400


def test_concurrent_page_loads_do_not_fail(synthetic_data_dir: Path) -> None:
    """The framework runs a request's dependency setup, handler and teardown on
    worker threads that need not be the same one. A connection that insists on
    its creating thread turned that into intermittent 500s."""
    from concurrent.futures import ThreadPoolExecutor

    app = create_app(data_dir=synthetic_data_dir, config_dir=CONFIG_DIR)

    def load(_: int) -> int:
        with _client(app, raise_server_exceptions=False) as client:
            return client.get("/").status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(load, range(120)))
    assert statuses.count(200) == len(statuses)


def test_double_submit_of_the_resolve_form_creates_one_application(
    dlq_data_dir: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two POSTs while the first is still inside its model call, the double-click
    shape. Both used to pass the 'still unresolved?' read and each created an
    Application."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from jscc import dlq

    real = dlq.extract_and_create_application
    entered = threading.Event()
    model_calls: list[int] = []

    def slow(*args, **kwargs):
        model_calls.append(1)
        entered.set()
        time.sleep(0.6)
        return real(*args, **kwargs)

    monkeypatch.setattr(dlq, "extract_and_create_application", slow)
    data_dir, entry_id = dlq_data_dir
    app = create_app(data_dir=data_dir, config_dir=CONFIG_DIR)

    def post(_: int) -> str:
        with _client(app) as client:
            return client.post(f"/dlq/{entry_id}/resolve", data=_PASTE).text

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(post, 0)
        assert entered.wait(timeout=5)
        second = pool.submit(post, 1)
        pages = [first.result(timeout=30), second.result(timeout=30)]

    assert len(_apps(data_dir)) == 1
    # The compare-and-set alone would also leave one Application, but only after
    # the second request had paid for its own model call.
    assert len(model_calls) == 1
    assert sum("Created application" in p for p in pages) == 1
    assert sum("already resolved" in p for p in pages) == 1


def test_no_page_loads_a_script_or_any_third_party_origin() -> None:
    """A script in the base template runs with read/write access to every
    real-mode page, and a CDN one is unpinned code from a third party."""
    from jscc.web.app import TEMPLATES_DIR

    for template in TEMPLATES_DIR.glob("*.html"):
        text = template.read_text(encoding="utf-8").lower()
        assert "<script" not in text, template.name
        for scheme in ("http://", "https://"):
            assert scheme not in text, f"{template.name} references {scheme}"


def test_a_non_http_source_url_is_shown_but_not_linked(
    synthetic_data_dir: Path,
) -> None:
    conn = open_for_mode(Mode.synthetic, synthetic_data_dir)
    try:
        application = Application(
            company="Zed", title="Engineer", stage="lead", source_url="javascript:alert(1)"
        )
        create_application(conn, application)
    finally:
        conn.close()
    client = _client(create_app(data_dir=synthetic_data_dir, config_dir=CONFIG_DIR))

    page = client.get(f"/applications/{application.id}").text

    assert "javascript:alert(1)" in page
    assert 'href="javascript:' not in page


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("localhost", "localhost"),
        ("LocalHost:8000", "localhost"),
        ("127.0.0.1:8000", "127.0.0.1"),
        ("[::1]:8000", "::1"),
        ("[::1]", "::1"),
        ("attacker.example:80", "attacker.example"),
        ("", ""),
    ],
)
def test_hostname_parses_the_host_header(header: str, expected: str) -> None:
    from jscc.web.app import _hostname

    assert _hostname(header) == expected


def test_the_request_connection_may_cross_threads(
    synthetic_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The framework runs a request's setup, handler and teardown on worker
    threads that need not match; the connection must not pin to its creator."""
    import jscc.web.app as web_app

    seen: list[bool] = []
    real_open = web_app.open_for_mode

    def spy(mode, data_dir, **kwargs):
        seen.append(kwargs.get("check_same_thread", True))
        return real_open(mode, data_dir, **kwargs)

    monkeypatch.setattr(web_app, "open_for_mode", spy)
    _client(create_app(data_dir=synthetic_data_dir, config_dir=CONFIG_DIR)).get("/")
    assert seen == [False]


def test_resolve_form_reports_a_duplicate_instead_of_creating_one(
    dlq_data_dir: tuple[Path, str],
) -> None:
    data_dir, entry_id = dlq_data_dir
    conn = open_for_mode(Mode.synthetic, data_dir)
    try:
        create_application(
            conn,
            Application(
                company="Rift",
                title="Engineer",
                stage="identified",
                source_url="https://example.com/jobs/5",
            ),
        )
    finally:
        conn.close()
    client = _client(create_app(data_dir=data_dir, config_dir=CONFIG_DIR))

    page = client.post(f"/dlq/{entry_id}/resolve", data=_PASTE).text

    assert "already exists for this posting" in page
    assert len(_apps(data_dir)) == 1


def test_real_mode_resolve_without_a_key_refuses_instead_of_saving_a_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a key, extraction falls back to a stub that returns fixed text. In
    synthetic mode that is the demo; in real mode it would store a made-up
    application beside real ones."""
    monkeypatch.setenv(ENV_VAR, "real")
    conn = open_for_mode(Mode.real, tmp_path)
    try:
        entry_id = create_dlq_entry(
            conn,
            DLQEntry(source_url="https://example.com/jobs/5", failure_mode=FailureMode.blocked),
        )
    finally:
        conn.close()
    client = _client(create_app(data_dir=tmp_path, config_dir=CONFIG_DIR))

    page = client.post(f"/dlq/{entry_id}/resolve", data=_PASTE).text

    assert "ANTHROPIC_API_KEY" in page
    conn = open_for_mode(Mode.real, tmp_path)
    try:
        assert list_applications(conn) == []
    finally:
        conn.close()


def test_a_future_timestamp_is_a_named_error_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    conn.close()
    _insert_app(tmp_path, title="Odd", company="Acme", stage="applied", days_ago=-4000)
    client = _client(
        create_app(data_dir=tmp_path, config_dir=CONFIG_DIR), raise_server_exceptions=False
    )

    response = client.get("/")

    assert response.status_code == 500
    assert "future reference timestamp" in response.text
