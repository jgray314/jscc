# Changelog

## [Unreleased]

### A3 hardening — timeline coherence + contact wiring + JD variety
- Interaction chains are now anchored on `applied_at` and stepped forward with realistic gaps, producing chronologically-ordered timelines.
- `Application.last_interaction_at` now equals the chain-end timestamp (previously drifted).
- HM screen and onsite interactions reference the HM `contact_id` (previously always null).
- `closed` applications now truncate at variable chain depth (early close vs. late close), weighted realistically.
- Responsibility strings pulled from role-typed pools (platform / ML / growth / payments / reliability / devex / data / general) so extracted_jd content varies across the fixture.
- 4 new pytest cases (38 total): chronological ordering, HM-contact-referenced-when-present, responsibility variety, `last_interaction_at` matches chain end.

### A3 — Synthetic seed generator
- `jscc/seed.py`: deterministic (RNG-seeded) synthetic fixture — 25 applications distributed across all pipeline stages, 19 contacts (recruiter + HM chained by stage progression), 40 interactions (applied → recruiter reply → screen → onsite → rejection where applicable), 3 DLQ entries (paywall / blocked / timeout).
- New CLI: `python -m jscc seed --synthetic` — supports `--random-seed`, `--db-path`, `--no-reset`. Wipes tables by default so re-runs are stable.
- Default DB path shifted from `data/dev.db` to `data/synthetic.db` — aligns with D7 M7 convention pre-A4.5, avoids a rename.
- Fake data hygiene: obviously-synthetic company names and role-tag "Placeholder" contact names — no personal-data-shaped strings per D8 principle.
- 6 new pytest cases (34 total): determinism, seed-differs-with-seed, stage distribution, fresh/stale timestamp mix, end-to-end roundtrip through storage, reset behavior.

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
