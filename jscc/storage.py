"""SQLite persistence layer — the second choke point after the sanitizer.

Two rules that make the mode-safety story structural rather than disciplinary:

1. **Every production DB open goes through `open_for_mode`.** That function
   creates only the meta table on a bare connection, verifies the stamped mode
   marker matches the caller's intent, and only then runs the DDL. The
   primitives underneath — `_connect` and `_init_db` — are underscored on
   purpose. If you need one from outside this module, you are about to
   accidentally recreate the C4/C5 bug class the A6 hardening slice closed.
2. **`__all__` names the safe surface only.** `import *` will not reach the
   primitives; the safe entry points are `open_for_mode` plus the CRUD helpers
   (which all take a `sqlite3.Connection` an `open_for_mode` caller already
   validated).

Tests can still import the underscored names — they need them to construct
pre-corrupt / pre-populated fixtures the safe front door refuses to create.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .mode import Mode, resolve_db_path
from .models import (
    Application,
    Contact,
    DLQEntry,
    Interaction,
    LLMCallRecord,
    Resolution,
    _now,
)


__all__ = [
    "ModeMismatchError",
    "DB_SCHEMA_VERSION",
    "open_for_mode",
    "read_mode_marker",
    "write_mode_marker",
    "resolve_db_path",
    "schema_version",
    "create_application",
    "create_contact",
    "create_interaction",
    "create_dlq_entry",
    "update_application",
    "resolve_dlq_entry",
    "list_applications",
    "list_contacts",
    "list_interactions",
    "list_dlq_entries",
    "reset_tables",
    "record_llm_call",
    "list_llm_calls",
]


class ModeMismatchError(RuntimeError):
    """A DB stamped with one mode is being opened under a different mode."""


DB_SCHEMA_VERSION = 3

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

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id TEXT PRIMARY KEY,
    feature TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    latency_ms REAL NOT NULL,
    ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_llm_calls_feature ON llm_calls(feature);
CREATE INDEX IF NOT EXISTS ix_llm_calls_ts ON llm_calls(ts);
"""

_MODE_META_KEY = "mode"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Low-level DB open. **Private on purpose.**

    Everything that opens a DB in production must go through `open_for_mode` so
    the mode marker is verified first. `_connect` is the primitive underneath;
    tests use it to construct pre-corrupt / pre-populated fixtures that would be
    impossible to build through the safe front door. Never import this from
    outside the jscc package or its tests.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # M4: keep concurrent CLI invocations (seed + report, or Phase B agent
    # workers) from spuriously getting `database is locked`. WAL is safe for
    # this workload (single-machine, filesystem-local); busy_timeout gives
    # writers 5s of retry room before raising.
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_DDL)
    conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
    conn.commit()


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _db_has_user_tables(conn: sqlite3.Connection) -> bool:
    """True iff the DB has any table other than the mode-marker `meta` table.

    Used by `open_for_mode` to distinguish "fresh DB, safe to init" from
    "populated DB whose marker row is missing" (which should refuse, not
    silently restamp).
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' AND name != 'meta'"
    ).fetchall()
    return len(rows) > 0


def read_mode_marker(conn: sqlite3.Connection) -> Mode | None:
    """Return the stamped mode for a DB, or None if not yet stamped / no meta table.

    Raises `ModeMismatchError` if the marker row exists but its value is not
    a valid `Mode`. A corrupt marker is a structural safety failure, not a
    generic value error — surfacing it as `ValueError` would leak past any
    `except ModeMismatchError` guard in the caller.
    """
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_MODE_META_KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    raw = row["value"]
    try:
        return Mode(raw)
    except ValueError as e:
        raise ModeMismatchError(
            f"database has corrupt mode marker {raw!r}: {e}"
        ) from None


def write_mode_marker(conn: sqlite3.Connection, mode: Mode) -> None:
    """Stamp the DB with a mode marker. Overwrites any existing value."""
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (_MODE_META_KEY, mode.value),
    )
    conn.commit()


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    """Create only the `meta` table. Used to inspect a DB before deciding
    whether to run full DDL — the full DDL must NEVER run against a DB that
    was populated under a different mode (Phase A adversarial finding C4)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()


def open_for_mode(
    mode: Mode,
    data_dir: Path | None = None,
) -> sqlite3.Connection:
    """Open (and initialize on first use) the DB corresponding to `mode`.

    Two paths, chosen by whether the DB is a fresh file or a populated one:

    * **Fresh** (no user tables): run full DDL, stamp the mode marker.
    * **Populated**: verify the mode marker exists AND matches. If the row
      is missing (a populated DB with no `meta.mode`) — refuse. That state
      is only reachable via bug/tampering; silently restamping it would
      overwrite an unknown-mode file with the caller's mode. Ordering
      matters: full DDL must NEVER run against a populated wrong-mode DB
      (adversarial finding C4 — the previous ordering ran DDL first and
      bumped `PRAGMA user_version` on the wrong file before catching the
      mismatch).

    Raises `ModeMismatchError` on any of: mismatched marker, missing marker
    on a populated DB, corrupt marker value. Connection is always closed
    before raising (try/finally-equivalent via close-then-raise; no error
    can slip past because sqlite3 close is best-effort and does not raise
    on a bare read connection).
    """
    path = resolve_db_path(mode, data_dir)
    conn = _connect(path)
    try:
        _ensure_meta_table(conn)
        populated = _db_has_user_tables(conn)
        stamped = read_mode_marker(conn)  # may raise ModeMismatchError

        if populated:
            if stamped is None:
                raise ModeMismatchError(
                    f"database at {path} is populated but has no mode marker; "
                    f"refusing to open (would silently restamp under {mode.value!r})."
                )
            if stamped != mode:
                raise ModeMismatchError(
                    f"database at {path} was stamped as {stamped.value!r}; "
                    f"refusing to open under mode {mode.value!r} (JSCC_DATA)."
                )
            # Populated + matches: bring schema up to date (idempotent DDL is
            # safe now that we know the mode agrees).
            _init_db(conn)
            return conn

        # Fresh DB path: run full init, stamp the marker.
        _init_db(conn)
        write_mode_marker(conn, mode)
        return conn
    except BaseException:
        # Best-effort close: a raise from conn.close() here (rare, but possible
        # on a WAL sidecar I/O failure) would replace the original exception
        # — including the safety-critical ModeMismatchError — with a generic
        # disk error, and the developer would have no idea their JSCC_DATA was
        # pointed at the wrong file.
        try:
            conn.close()
        except Exception:
            pass
        raise


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


def _json_default(obj: Any) -> Any:
    """Fallback serializer for `_dump_json`. Handles the value types Phase B
    is likely to embed in `extracted_jd` (datetimes, sets, pydantic models,
    enums) without silently swallowing types the schema hasn't decided about."""
    from enum import Enum
    from pydantic import BaseModel

    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        # Sort for determinism; if elements aren't comparable, TypeError bubbles
        # up as a real schema-design signal rather than being silently masked.
        return sorted(obj, key=repr)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON-serializable")


def _dump_json(value: Any) -> str | None:
    return (
        None
        if value is None
        else json.dumps(value, sort_keys=True, default=_json_default)
    )


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
    now: datetime | None = None,
) -> None:
    if resolution is Resolution.unresolved:
        raise ValueError("cannot resolve to 'unresolved'; use one of manual_paste, wont_fix")
    stamped_at = now if now is not None else _now()
    conn.execute(
        "UPDATE dlq_entries SET resolution = ?, resolved_at = ? WHERE id = ?",
        (resolution.value, _iso(stamped_at), entry_id),
    )
    conn.commit()


# ---- LLM call ledger (D5) ------------------------------------------------------

def record_llm_call(conn: sqlite3.Connection, record: LLMCallRecord) -> str:
    conn.execute(
        """
        INSERT INTO llm_calls (
            id, feature, model, prompt_hash,
            input_tokens, output_tokens, cost_usd, latency_ms, ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.id, record.feature, record.model, record.prompt_hash,
            record.input_tokens, record.output_tokens,
            record.cost_usd, record.latency_ms, _iso(record.ts),
        ),
    )
    conn.commit()
    return record.id


def list_llm_calls(conn: sqlite3.Connection) -> list[LLMCallRecord]:
    rows = conn.execute("SELECT * FROM llm_calls ORDER BY ts").fetchall()
    return [
        LLMCallRecord(
            id=r["id"],
            feature=r["feature"],
            model=r["model"],
            prompt_hash=r["prompt_hash"],
            input_tokens=r["input_tokens"],
            output_tokens=r["output_tokens"],
            cost_usd=r["cost_usd"],
            latency_ms=r["latency_ms"],
            ts=_parse_dt(r["ts"]),
        )
        for r in rows
    ]
