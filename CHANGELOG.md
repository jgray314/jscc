# Changelog

## [Unreleased]

### A4.5b — Content controls (D7 M3/M5)
- `scripts/precommit_scan.py`: standalone Python scanner. Rules: email regex, phone regex (validated by digit-count 10-15 to defuse ISO-date false positives), and case-insensitive substring match against `.safety/danger-list.txt`. Reports every hit with `<file>:<line>: <reason> -- <match>` on stderr, exits 1 on any hit. Skips binaries. Standalone-invokable so tests exercise the exact commit-time code path.
- `.pre-commit-config.yaml`: local hook wrapping the scanner. Excludes `data/`, the scanner itself, and its test file to avoid self-match.
- `.safety/danger-list.txt`: committed scaffold with header comments only — real identifiers go in a local override so the repo itself doesn't leak the terms it guards.
- `jscc/sanitizer.py`: `sanitize_for_llm(payload)` skeleton — pass-through that stamps a `_sanitized_at` UTC ISO-8601 marker; refuses payloads flagged `contains_personal=True` with `SanitizerRefusal`. Interface locked so Phase B slices can import stably; substantive redaction rules land with the first LLM call.
- 22 new pytest cases (86 total): sanitizer roundtrip + marker + refusal + shallow-copy + type-check + `is_sanitized`; scanner email/phone/danger-list hits, case-insensitive matching, comment/blank stripping, seed-fake-data-passes gotcha check, ISO-date and short-version regression, binary skip, missing-file/missing-danger-list tolerance, multi-hit reporting.
- ADR-004 documents `pre-commit.com` + local Python hook choice with alternatives (native hook, GHA-only, husky, commit-msg stage, larger regex battery).

### A4.5a — Environment isolation (D7 M1/M2/M7)
- `jscc/mode.py`: `Mode` enum (`synthetic` / `real`) selected by `JSCC_DATA` env var (default synthetic). `resolve_db_path(mode)` → `data/<mode>.db`.
- `storage.py`: `open_for_mode()` opens the mode's DB, ensures schema is initialized, stamps the mode marker on first use, verifies it on subsequent opens. `ModeMismatchError` on cross-mode open. Schema bumped v1 → v2 (new `meta` table).
- CLI routes every DB access through `open_for_mode`. `--db-path` overrides removed; `--data-dir` added (default `data/`). `seed --synthetic` refuses when `JSCC_DATA=real`.
- `config.yaml` renamed `profile.yaml` → `profile.example.yaml`. New `resolve_profile_path()` prefers `profile.private.yaml` when present, else falls back to example.
- `.gitignore`: added SQLite journal patterns (`data/*.db-shm`, `data/*.db-wal`, `data/*.db-journal`); confirmed existing D7 M2 coverage.
- 14 new pytest cases (64 total): default-is-synthetic, env resolution (valid/bogus/empty/override), path convention, marker stamp on first use, reopen-same-mode ok, cross-mode raises `ModeMismatchError`, two DBs coexist; profile private-then-example fallback; profile-not-found raises.
- ADR-003 documents mode-isolation architecture with alternatives considered (single-file+mode-column, path-only-no-marker, hard-refuse-real, per-mode .env).

### A4 — Staleness detector + funnel counts
- `jscc/report.py`: pure functions `funnel_counts()` and `detect_stale()` over `list[Application]` + `StagesConfig`. `StaleAlert` model with `overdue_by_days`. `format_report()` renders a text summary.
- Staleness reference timestamp is `last_interaction_at` when set, else `created_at` (covers identified-stage apps with no interactions).
- Alerts sorted most-overdue-first; unknown stages skipped; naive datetimes handled per ADR-002 contract.
- New CLI: `python -m jscc report` — funnel by configured stage order (zero counts included), then stale list.
- 12 new pytest cases (50 total): funnel with zeros / unknown stage; stale detection over/under/at-threshold; ordering; `created_at` fallback; unknown-stage skip; high-threshold-excludes-closed; naive-datetime handling; format-report structure; empty case; end-to-end against seeded fixture.

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
