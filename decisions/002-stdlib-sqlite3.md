# ADR 002: stdlib sqlite3 for storage, no ORM

**Status:** Accepted (2026-08-28, Slice A2)

## Context

JSCC needs to persist ~4 tables (`applications`, `contacts`, `interactions`, `dlq_entries`) locally on the user's laptop. Query patterns are small: fetch by primary key, filter by one indexed column, list by application. No concurrent writers. No cross-machine sync. This is a personal tool run interactively.

## Decision

Use Python's stdlib `sqlite3` module directly. Hand-written SQL in `jscc/storage.py`. Domain objects are pydantic models (from `jscc/models.py`) with row-to-model helpers at the storage boundary.

## Alternatives considered

- **SQLAlchemy Core.** Familiar to reviewers; would give portable SQL generation. Rejected — expression trees and metadata objects outweigh the DDL of ~4 tables. Migration to it later is straightforward if the schema grows.
- **SQLAlchemy ORM.** Identity map, sessions, lazy loading — all valuable in multi-user backend systems and all overhead here. Rejected.
- **SQLModel.** Pydantic + SQLAlchemy hybrid. Nice ergonomics but adds a magic layer between reader and behavior. Rejected — cost of understanding exceeds the value it removes.
- **`aiosqlite` for async.** No async surface today; no callers that would benefit. Rejected.

## Consequences

- Positive: reader can trace any query in one file without leaving the module.
- Positive: portfolio signal — reaching for the lightest tool that fits is a real judgment marker.
- Positive: no framework-shaped constraints on schema evolution; migrations are versioned by `PRAGMA user_version` (see `DB_SCHEMA_VERSION`) with hand-written upgrade scripts when the second version arrives.
- Cost: every entity needs a hand-written row-to-model helper. Acceptable at this scale; watch for it as friction if the model grows large.
- Cost: no compile-time protection against typos in column names. Test coverage on CRUD is the mitigation (`tests/test_storage.py`).
- Revisit if: (a) we add a real remote deployment, (b) a second writer joins the picture, or (c) the CRUD surface triples.
