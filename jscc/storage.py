from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Application,
    Contact,
    DLQEntry,
    Interaction,
    Resolution,
    _now,
)

DB_SCHEMA_VERSION = 1

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    source_url TEXT,
    source_raw TEXT NOT NULL DEFAULT '',
    fetch_status TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    extracted_jd TEXT,             -- JSON
    stage TEXT NOT NULL,
    fit_score REAL,
    fit_rationale TEXT,
    applied_at TEXT,               -- ISO date
    created_at TEXT NOT NULL,      -- ISO datetime (UTC)
    updated_at TEXT NOT NULL,      -- ISO datetime (UTC)
    last_interaction_at TEXT       -- ISO datetime (UTC)
);

CREATE INDEX IF NOT EXISTS ix_applications_stage ON applications(stage);
CREATE INDEX IF NOT EXISTS ix_applications_last_interaction ON applications(last_interaction_at);

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    notes TEXT,
    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_contacts_application ON contacts(application_id);

CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    contact_id TEXT,
    type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    next_action TEXT,
    next_action_due TEXT,
    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE,
    FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_interactions_application ON interactions(application_id);
CREATE INDEX IF NOT EXISTS ix_interactions_occurred ON interactions(occurred_at);

CREATE TABLE IF NOT EXISTS dlq_entries (
    id TEXT PRIMARY KEY,
    application_id TEXT,
    source_url TEXT NOT NULL,
    failure_mode TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    error_detail TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_dlq_resolution ON dlq_entries(resolution);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_DDL)
    conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
    conn.commit()


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


# ---- serialization helpers ----------------------------------------------------

def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _dump_json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True)


def _load_json(value: str | None) -> Any:
    return None if value is None else json.loads(value)


# ---- Application --------------------------------------------------------------

def _application_row_to_model(row: sqlite3.Row) -> Application:
    return Application(
        id=row["id"],
        source_url=row["source_url"],
        source_raw=row["source_raw"],
        fetch_status=row["fetch_status"],
        title=row["title"],
        company=row["company"],
        extracted_jd=_load_json(row["extracted_jd"]),
        stage=row["stage"],
        fit_score=row["fit_score"],
        fit_rationale=row["fit_rationale"],
        applied_at=_parse_date(row["applied_at"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        last_interaction_at=_parse_dt(row["last_interaction_at"]),
    )


def create_application(conn: sqlite3.Connection, app: Application) -> str:
    conn.execute(
        """
        INSERT INTO applications (
            id, source_url, source_raw, fetch_status, title, company,
            extracted_jd, stage, fit_score, fit_rationale,
            applied_at, created_at, updated_at, last_interaction_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app.id, app.source_url, app.source_raw, app.fetch_status.value,
            app.title, app.company, _dump_json(app.extracted_jd), app.stage,
            app.fit_score, app.fit_rationale,
            _iso(app.applied_at), _iso(app.created_at), _iso(app.updated_at),
            _iso(app.last_interaction_at),
        ),
    )
    conn.commit()
    return app.id


def get_application(conn: sqlite3.Connection, app_id: str) -> Application | None:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return _application_row_to_model(row) if row else None


def list_applications(
    conn: sqlite3.Connection,
    *,
    stage: str | None = None,
) -> list[Application]:
    if stage is None:
        rows = conn.execute("SELECT * FROM applications ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM applications WHERE stage = ? ORDER BY created_at",
            (stage,),
        ).fetchall()
    return [_application_row_to_model(r) for r in rows]


_UPDATABLE_APPLICATION_FIELDS = {
    "source_url", "source_raw", "fetch_status", "title", "company",
    "extracted_jd", "stage", "fit_score", "fit_rationale",
    "applied_at", "last_interaction_at",
}


def update_application(conn: sqlite3.Connection, app_id: str, **fields: Any) -> None:
    unknown = set(fields) - _UPDATABLE_APPLICATION_FIELDS
    if unknown:
        raise ValueError(f"cannot update fields: {sorted(unknown)}")
    if not fields:
        return
    cols: list[str] = []
    vals: list[Any] = []
    for key, value in fields.items():
        if key == "extracted_jd":
            vals.append(_dump_json(value))
        elif key in {"applied_at"}:
            vals.append(_iso(value))
        elif key in {"last_interaction_at"}:
            vals.append(_iso(value))
        elif key == "fetch_status" and hasattr(value, "value"):
            vals.append(value.value)
        else:
            vals.append(value)
        cols.append(f"{key} = ?")
    cols.append("updated_at = ?")
    vals.append(_iso(_now()))
    vals.append(app_id)
    conn.execute(
        f"UPDATE applications SET {', '.join(cols)} WHERE id = ?",
        vals,
    )
    conn.commit()


# ---- Contact ------------------------------------------------------------------

def create_contact(conn: sqlite3.Connection, contact: Contact) -> str:
    conn.execute(
        """
        INSERT INTO contacts (id, application_id, name, role, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (contact.id, contact.application_id, contact.name, contact.role.value, contact.notes),
    )
    conn.commit()
    return contact.id


def get_contact(conn: sqlite3.Connection, contact_id: str) -> Contact | None:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not row:
        return None
    return Contact(
        id=row["id"],
        application_id=row["application_id"],
        name=row["name"],
        role=row["role"],
        notes=row["notes"],
    )


def list_contacts(conn: sqlite3.Connection, application_id: str) -> list[Contact]:
    rows = conn.execute(
        "SELECT * FROM contacts WHERE application_id = ? ORDER BY name",
        (application_id,),
    ).fetchall()
    return [
        Contact(
            id=r["id"],
            application_id=r["application_id"],
            name=r["name"],
            role=r["role"],
            notes=r["notes"],
        )
        for r in rows
    ]


# ---- Interaction --------------------------------------------------------------

def create_interaction(conn: sqlite3.Connection, interaction: Interaction) -> str:
    conn.execute(
        """
        INSERT INTO interactions (
            id, application_id, contact_id, type,
            occurred_at, notes, next_action, next_action_due
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interaction.id, interaction.application_id, interaction.contact_id,
            interaction.type.value, _iso(interaction.occurred_at), interaction.notes,
            interaction.next_action, _iso(interaction.next_action_due),
        ),
    )
    conn.commit()
    return interaction.id


def list_interactions(
    conn: sqlite3.Connection, application_id: str
) -> list[Interaction]:
    rows = conn.execute(
        "SELECT * FROM interactions WHERE application_id = ? ORDER BY occurred_at",
        (application_id,),
    ).fetchall()
    return [
        Interaction(
            id=r["id"],
            application_id=r["application_id"],
            contact_id=r["contact_id"],
            type=r["type"],
            occurred_at=_parse_dt(r["occurred_at"]),
            notes=r["notes"],
            next_action=r["next_action"],
            next_action_due=_parse_date(r["next_action_due"]),
        )
        for r in rows
    ]


# ---- DLQ ----------------------------------------------------------------------

def create_dlq_entry(conn: sqlite3.Connection, entry: DLQEntry) -> str:
    conn.execute(
        """
        INSERT INTO dlq_entries (
            id, application_id, source_url, failure_mode,
            attempted_at, error_detail, resolution, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.id, entry.application_id, entry.source_url,
            entry.failure_mode.value, _iso(entry.attempted_at),
            entry.error_detail, entry.resolution.value, _iso(entry.resolved_at),
        ),
    )
    conn.commit()
    return entry.id


def _dlq_row_to_model(row: sqlite3.Row) -> DLQEntry:
    return DLQEntry(
        id=row["id"],
        application_id=row["application_id"],
        source_url=row["source_url"],
        failure_mode=row["failure_mode"],
        attempted_at=_parse_dt(row["attempted_at"]),
        error_detail=row["error_detail"],
        resolution=row["resolution"],
        resolved_at=_parse_dt(row["resolved_at"]),
    )


def list_dlq_entries(
    conn: sqlite3.Connection, *, unresolved_only: bool = True
) -> list[DLQEntry]:
    if unresolved_only:
        rows = conn.execute(
            "SELECT * FROM dlq_entries WHERE resolution = ? ORDER BY attempted_at",
            (Resolution.unresolved.value,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM dlq_entries ORDER BY attempted_at"
        ).fetchall()
    return [_dlq_row_to_model(r) for r in rows]


def resolve_dlq_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    resolution: Resolution,
) -> None:
    if resolution is Resolution.unresolved:
        raise ValueError("cannot resolve to 'unresolved'; use one of manual_paste, wont_fix")
    conn.execute(
        "UPDATE dlq_entries SET resolution = ?, resolved_at = ? WHERE id = ?",
        (resolution.value, _iso(_now()), entry_id),
    )
    conn.commit()
