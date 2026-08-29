from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from jscc.models import (
    Application,
    Contact,
    ContactRole,
    DLQEntry,
    FailureMode,
    FetchStatus,
    Interaction,
    InteractionType,
    Resolution,
)
from jscc.storage import (
    DB_SCHEMA_VERSION,
    connect,
    create_application,
    create_contact,
    create_dlq_entry,
    create_interaction,
    get_application,
    get_contact,
    init_db,
    list_applications,
    list_contacts,
    list_dlq_entries,
    list_interactions,
    resolve_dlq_entry,
    schema_version,
    update_application,
)


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "test.db"
    c = connect(db_path)
    init_db(c)
    yield c
    c.close()


def _sample_app(**overrides) -> Application:
    defaults = dict(
        title="Staff Engineer",
        company="ExampleCo",
        stage="applied",
    )
    defaults.update(overrides)
    return Application(**defaults)


def test_init_creates_schema(conn: sqlite3.Connection) -> None:
    assert schema_version(conn) == DB_SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"applications", "contacts", "interactions", "dlq_entries"} <= tables


def test_init_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "twice.db"
    c = connect(db_path)
    init_db(c)
    init_db(c)  # must not raise
    assert schema_version(c) == DB_SCHEMA_VERSION
    c.close()


def test_application_roundtrip(conn: sqlite3.Connection) -> None:
    app = _sample_app(
        source_url="https://example.com/job/1",
        fit_score=82.5,
        fit_rationale="strong platform match",
        applied_at=date(2026, 8, 27),
    )
    create_application(conn, app)
    loaded = get_application(conn, app.id)
    assert loaded is not None
    assert loaded.title == "Staff Engineer"
    assert loaded.fit_score == 82.5
    assert loaded.applied_at == date(2026, 8, 27)
    assert loaded.fetch_status is FetchStatus.ok


def test_extracted_jd_json_roundtrip(conn: sqlite3.Connection) -> None:
    extracted = {
        "level": "L6",
        "responsibilities": ["own platform roadmap", "grow team"],
        "comp_band": {"min": 300000, "max": 450000, "currency": "USD"},
        "stack": ["python", "postgres", "kubernetes"],
    }
    app = _sample_app(extracted_jd=extracted)
    create_application(conn, app)
    loaded = get_application(conn, app.id)
    assert loaded is not None
    assert loaded.extracted_jd == extracted


def test_get_missing_application_returns_none(conn: sqlite3.Connection) -> None:
    assert get_application(conn, "does-not-exist") is None


def test_list_applications_filter_by_stage(conn: sqlite3.Connection) -> None:
    create_application(conn, _sample_app(title="A", stage="applied"))
    create_application(conn, _sample_app(title="B", stage="onsite"))
    create_application(conn, _sample_app(title="C", stage="applied"))
    applied = list_applications(conn, stage="applied")
    onsite = list_applications(conn, stage="onsite")
    assert {a.title for a in applied} == {"A", "C"}
    assert [a.title for a in onsite] == ["B"]
    assert len(list_applications(conn)) == 3


def test_update_application_touches_updated_at(conn: sqlite3.Connection) -> None:
    app = _sample_app()
    create_application(conn, app)
    before = get_application(conn, app.id)
    assert before is not None
    update_application(conn, app.id, stage="onsite", fit_score=91.0)
    after = get_application(conn, app.id)
    assert after is not None
    assert after.stage == "onsite"
    assert after.fit_score == 91.0
    assert after.updated_at >= before.updated_at


def test_update_application_rejects_unknown_field(conn: sqlite3.Connection) -> None:
    app = _sample_app()
    create_application(conn, app)
    with pytest.raises(ValueError, match="cannot update fields"):
        update_application(conn, app.id, id="something-else")


def test_contact_requires_existing_application(conn: sqlite3.Connection) -> None:
    contact = Contact(application_id="nope", name="Alex", role=ContactRole.recruiter)
    with pytest.raises(sqlite3.IntegrityError):
        create_contact(conn, contact)


def test_contact_cascade_delete(conn: sqlite3.Connection) -> None:
    app = _sample_app()
    create_application(conn, app)
    contact = Contact(application_id=app.id, name="Alex", role=ContactRole.hm)
    create_contact(conn, contact)
    assert get_contact(conn, contact.id) is not None
    conn.execute("DELETE FROM applications WHERE id = ?", (app.id,))
    conn.commit()
    assert get_contact(conn, contact.id) is None


def test_interaction_optional_contact_and_ordering(conn: sqlite3.Connection) -> None:
    app = _sample_app()
    create_application(conn, app)
    older = Interaction(
        application_id=app.id,
        type=InteractionType.applied,
        occurred_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    newer = Interaction(
        application_id=app.id,
        type=InteractionType.recruiter_reply,
        occurred_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        notes="scheduled screen for next week",
        next_action="prep questions",
        next_action_due=date(2026, 8, 25),
    )
    create_interaction(conn, older)
    create_interaction(conn, newer)
    loaded = list_interactions(conn, app.id)
    assert [i.type for i in loaded] == [InteractionType.applied, InteractionType.recruiter_reply]
    assert loaded[1].next_action_due == date(2026, 8, 25)


def test_list_contacts_orders_by_name(conn: sqlite3.Connection) -> None:
    app = _sample_app()
    create_application(conn, app)
    create_contact(conn, Contact(application_id=app.id, name="Zoe", role=ContactRole.hm))
    create_contact(conn, Contact(application_id=app.id, name="Alex", role=ContactRole.recruiter))
    names = [c.name for c in list_contacts(conn, app.id)]
    assert names == ["Alex", "Zoe"]


def test_dlq_lifecycle(conn: sqlite3.Connection) -> None:
    entry = DLQEntry(
        source_url="https://paywalled.example/job/9",
        failure_mode=FailureMode.paywall,
        error_detail="HTTP 402",
    )
    create_dlq_entry(conn, entry)
    pending = list_dlq_entries(conn)
    assert [e.id for e in pending] == [entry.id]

    resolve_dlq_entry(conn, entry.id, Resolution.manual_paste)
    remaining = list_dlq_entries(conn)
    assert remaining == []

    all_entries = list_dlq_entries(conn, unresolved_only=False)
    assert len(all_entries) == 1
    assert all_entries[0].resolution is Resolution.manual_paste
    assert all_entries[0].resolved_at is not None


def test_resolve_to_unresolved_rejected(conn: sqlite3.Connection) -> None:
    entry = DLQEntry(source_url="https://x", failure_mode=FailureMode.blocked)
    create_dlq_entry(conn, entry)
    with pytest.raises(ValueError, match="cannot resolve to 'unresolved'"):
        resolve_dlq_entry(conn, entry.id, Resolution.unresolved)


def test_naive_datetime_input_stored_and_read_as_utc(conn: sqlite3.Connection) -> None:
    """Callers may pass a naive datetime; storage treats it as UTC and reads back UTC-aware.

    Documents the naive-in / aware-out contract so callers relying on either behavior fail
    loudly if it changes.
    """
    app = _sample_app()
    create_application(conn, app)
    naive = datetime(2026, 8, 15, 12, 0, 0)
    interaction = Interaction(
        application_id=app.id,
        type=InteractionType.screen,
        occurred_at=naive,
    )
    create_interaction(conn, interaction)
    loaded = list_interactions(conn, app.id)[0]
    assert loaded.occurred_at.tzinfo is not None
    assert loaded.occurred_at.utcoffset() == timedelta(0)
    assert loaded.occurred_at.replace(tzinfo=None) == naive


def test_dlq_entry_survives_application_delete_with_null_fk(conn: sqlite3.Connection) -> None:
    """DLQ entries tied to an Application must not be cascade-deleted; FK is SET NULL."""
    app = _sample_app()
    create_application(conn, app)
    entry = DLQEntry(
        application_id=app.id,
        source_url="https://timeout.example/job/7",
        failure_mode=FailureMode.timeout,
    )
    create_dlq_entry(conn, entry)
    conn.execute("DELETE FROM applications WHERE id = ?", (app.id,))
    conn.commit()
    remaining = list_dlq_entries(conn, unresolved_only=False)
    assert len(remaining) == 1
    assert remaining[0].id == entry.id
    assert remaining[0].application_id is None


def test_update_extracted_jd_via_update_path(conn: sqlite3.Connection) -> None:
    """The update path serializes extracted_jd through the JSON boundary."""
    app = _sample_app(extracted_jd={"level": "L5", "stack": ["python"]})
    create_application(conn, app)
    replacement = {"level": "L7", "stack": ["python", "go"], "responsibilities": ["own X"]}
    update_application(conn, app.id, extracted_jd=replacement)
    loaded = get_application(conn, app.id)
    assert loaded is not None
    assert loaded.extracted_jd == replacement

    update_application(conn, app.id, extracted_jd=None)
    cleared = get_application(conn, app.id)
    assert cleared is not None
    assert cleared.extracted_jd is None


def test_connect_creates_missing_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "test.db"
    assert not nested.parent.exists()
    c = connect(nested)
    try:
        init_db(c)
        assert nested.parent.exists()
        assert nested.exists()
    finally:
        c.close()


def test_extracted_jd_serializes_datetime_set_enum(tmp_path: Path) -> None:
    """H4 regression: _dump_json must handle types Phase B extraction will produce."""
    c = connect(tmp_path / "test.db")
    try:
        init_db(c)
        exotic = {
            "posted_at": datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
            "seen_on": date(2026, 8, 1),
            "tags": {"remote", "senior", "python"},
            "fetch_status": FetchStatus.ok,
        }
        app = Application(
            id="app-h4",
            title="SWE",
            company="H4Co",
            fetch_status=FetchStatus.ok,
            stage="applied",
            extracted_jd=exotic,
        )
        create_application(c, app)
        loaded = get_application(c, "app-h4")
        assert loaded is not None
        jd = loaded.extracted_jd
        assert jd["posted_at"] == "2026-07-04T12:00:00+00:00"
        assert jd["seen_on"] == "2026-08-01"
        assert jd["tags"] == sorted(["remote", "senior", "python"], key=repr)
        assert jd["fetch_status"] == "ok"
    finally:
        c.close()


def test_extracted_jd_serializes_nested_pydantic_model(tmp_path: Path) -> None:
    """H4: nested BaseModel in extracted_jd is model_dump()'d, not TypeError'd."""
    c = connect(tmp_path / "test.db")
    try:
        init_db(c)
        nested_contact = Contact(
            id="c1", application_id="app-h4b", name="Recruiter A. Placeholder",
            role=ContactRole.recruiter,
        )
        app = Application(
            id="app-h4b",
            title="SWE",
            company="H4Co",
            fetch_status=FetchStatus.ok,
            stage="applied",
            extracted_jd={"nested": nested_contact},
        )
        create_application(c, app)
        loaded = get_application(c, "app-h4b")
        assert loaded is not None
        assert loaded.extracted_jd["nested"]["name"] == "Recruiter A. Placeholder"
        assert loaded.extracted_jd["nested"]["role"] == "recruiter"
    finally:
        c.close()


def test_extracted_jd_unknown_type_raises_typerror(tmp_path: Path) -> None:
    """H4: types the fallback doesn't handle raise a clear TypeError (schema signal),
    not a silent swallow. Uses a class the fallback deliberately doesn't cover."""
    c = connect(tmp_path / "test.db")
    try:
        init_db(c)
        class Weird:
            pass
        app = Application(
            id="app-h4c",
            title="SWE",
            company="H4Co",
            fetch_status=FetchStatus.ok,
            stage="applied",
            extracted_jd={"weird": Weird()},
        )
        with pytest.raises(TypeError, match="not JSON-serializable"):
            create_application(c, app)
    finally:
        c.close()
