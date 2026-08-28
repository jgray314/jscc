# Changelog

## [Unreleased]

### A2 hardening — coverage gap-close
- Tests added: naive-datetime input round-trips as UTC-aware; DLQ entry survives Application delete with `application_id` set to NULL (verifies `ON DELETE SET NULL`); `update_application` extracted_jd flow through JSON serializer; `connect()` creates missing parent directories.
- ADR-002 documents single-writer-only limit and naive-datetime contract.

### A2 — Storage layer
- pydantic domain models: Application, Contact, Interaction, DLQEntry (+ FetchStatus / ContactRole / InteractionType / FailureMode / Resolution enums).
- SQLite schema for all four tables with foreign keys, cascade-delete on Application → Contact/Interaction, set-null on Contact deletion, indices on hot query paths.
- CRUD: `create_*`, `get_*`, `list_*` for each entity; `update_application` with field-whitelist and auto-touched `updated_at`; DLQ lifecycle via `create_dlq_entry` + `resolve_dlq_entry`.
- New CLI: `python -m jscc db init` (idempotent).
- Schema versioning via `PRAGMA user_version`.
- 14 additional pytest cases (24 total), including cascade-delete behavior, JSON roundtrip on `extracted_jd`, FK enforcement, DLQ lifecycle.
- ADR-002 documents stdlib `sqlite3` over SQLAlchemy/SQLModel with rejected alternatives.

### A1 — Repo scaffold + config loader
- Bootstrapped `jscc` Python package under the portfolio repo.
- Added pydantic-based config models for `stages.yaml` and `profile.yaml`.
- Wired `python -m jscc validate-config` — exits 0 on valid config, non-zero on broken.
- Unit tests: valid load, missing required field, bad type, unknown-field tolerance.
- Decisions: pydantic v2 over jsonschema (typed models double as runtime validators + docs); JSCC ships as its own repo linked from the `ai-portfolio` index.
