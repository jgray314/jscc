# Changelog

## [Unreleased]

### A9 — Rerun-gate closure (adversarial CRITICAL + walkthrough polish)
Rerun of both Phase A → B gates. Adversarial rerun returned BLOCK on one CRITICAL — a regression of H3 that A7 claimed closed. Walkthrough rerun returned "almost ready, no blockers". This slice closes everything that a fresh reviewer would trip on before Phase B lands.

- **C-seed-1 CRITICAL fix.** Only the first `Interaction` (the `applied` event) had `id=_rng_uuid(rng)` — the other five (`recruiter_reply`, HM `screen`, technical `screen`, `onsite`, `rejection`) fell back to `default_factory=uuid4` → `os.urandom`, so two runs with the same `--random-seed` and `--now` produced different `interactions.id` bytes. The A7 reproducibility test queried only the `applications` table (which was clean), so the regression was invisible until the adversarial pass found it. `resolve_dlq_entry` also used wall-clock time; now accepts an explicit `now`. All construction sites in `seed.py` now pass explicit seeded IDs, and the reproducibility test hashes every table (`applications`, `contacts`, `interactions`, `dlq_entries`).
- **H2 IDN email regex.** ASCII-only regex missed IDN local parts (`münchen@…`), non-ASCII TLDs (`.москва`), and Punycode TLDs (`.xn--p1ai`). Widened to `[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>().]{2,}` — deliberately over-broad per D7's "false positives are the design point" contract. Three new tests.
- **M1 `connect` / `init_db` bypass surface.** Renamed to `_connect` / `_init_db` — the safe DB open path (`open_for_mode`) is now the only public one. Added `__all__` naming the safe surface. New lock test asserts `not hasattr(jscc.storage, "connect")` so a future contributor cannot re-expose the primitive by accident.
- **M4 `busy_timeout` + WAL.** `_connect` now sets `PRAGMA busy_timeout = 5000` and `PRAGMA journal_mode = WAL`. Concurrent `seed` + `report` (and Phase B agent workers) no longer race to `database is locked`.
- **M6 future timestamps raise.** `detect_stale` no longer silently drops `days < 0` — a future reference timestamp now raises `ValueError`. Fixed the seed as the source: chain generation is capped at `now`, so synthetic fixtures cannot themselves emit future last-interaction timestamps.
- **M-exclude-1 `**` recursion.** Scanner `--exclude` was using `fnmatch`, which treats `**` as literal — `tests/**` had a silent hole where anything under nested subdirs still got scanned. Replaced with a real glob-to-regex compiler: `**` = any run of chars including `/`, `*` = any run excluding `/`, `?` = one non-`/` char. Two new tests.
- **Walkthrough polish.** README CI badge, refreshed sample `report` output (post-M6 seed clamp), corrected test count (135), expanded repo-layout tree to name every module. `storage.py` module docstring documents the safe-surface rule.
- **Storage module docstring.** Documents `open_for_mode` as the only production entry point and explains why `_connect` / `_init_db` are underscored.
- 7 new pytest cases (135 total): 3 IDN email (local / non-ASCII TLD / Punycode TLD), 1 M6 future-timestamp raise, 1 M1 lock test, 2 M-exclude-1 (recursive `**`, single `*` does not cross slash).

### A8 — Reviewer polish + CI
Portfolio-quality polish so a cold reviewer can grok the project in under five minutes, plus continuous coverage that the safety scanner and pytest suite both stay green.

- **README rewrite.** Three-idea framing (eval discipline, dual-use safety, when-not-to-automate), quick-start block, reproducible sample `report` output, repo layout, ADR link section, license.
- **`docs/design-principles.md`.** D1-D10 principles inlined for cold reviewers who don't want to hunt through five ADRs to find the frame. Each principle records what was chosen, why, and the alternative considered.
- **LICENSE.** MIT, Jess Gray 2026.
- **GH Actions CI (`ci.yml`).** `uv sync` + `pytest -q` + safety scanner sweep across every tracked file on `ubuntu-latest`, Python 3.12. Runs on push to main and every PR.
- **Scanner `--exclude` glob (pulled forward from rerun-gate M5).** CI would always be red without this: the scanner's own test file, its docstring, and CHANGELOG entries describing it all contain deliberately-placeholder personal-data shapes (`alice@example.com`, `(415) 555-0134`, etc.). New `--exclude GLOB` flag (repeatable, fnmatch on POSIX repo-relative paths) skips those files. CI passes `--exclude 'tests/test_precommit_scan.py' --exclude 'scripts/precommit_scan.py' --exclude 'CHANGELOG.md'`. 2 new pytest cases (128 total): single-file exclude, `**` pattern exclude.
- **`seed.py` module docstring reframe.** Now positions the seed as evaluation infrastructure with an explicit bit-reproducibility contract and a content contract, not just "the demo fixture."

### A7 — Phase A correctness + coverage gaps (adversarial review, H1/H3/M2/M3)
Closes the remaining HIGH and structural MEDIUM findings from the Phase A → B adversarial review.

- **Phone regex expansion (H1).** Character class widened to include `(`, `)`, `.` so `(415) 555-0134` and `+44.20.7946.0018` now match. Digit-count filter (10-15) still gates false positives; ISO-date regression test still passes. Danger-list scaffold's example phone rewritten as `XXX-XXX-XXXX` since the widened regex would self-match the previous `555-123-4567`.
- **Seed CLI `--now` flag (H3).** New `--now` option accepts a UTC ISO-8601 timestamp for reproducible fixtures; missing timezone or unparseable string raises `UsageError`. Without `--now`, seed continues to anchor on `datetime.now()` (documented as non-deterministic).
- **Seed ID determinism (H3, deeper).** Every model construction in `seed.py` now passes an explicit `id=_rng_uuid(rng)` derived from the seeded RNG. Previously pydantic's `default_factory=uuid4` bypassed the seed (uses `os.urandom`), so IDs and FK references varied run-to-run even with a pinned `--now`. Docstring's "deterministic for a given seed" claim now holds for real.
- **CLI test coverage (M3).** New `test_cli.py` via `click.testing.CliRunner`: 12 cases covering `validate-config` success/failure, `db init` synthetic/real/bogus-env, `seed` success + real-mode refusal + `--now` reproducibility + `--now` timezone/parse errors, `report` on seeded + empty DBs.
- **ATTACH DATABASE bypass surface (M2).** New test confirms that ATTACH-ing a real-mode DB into a synthetic-mode connection does not silently expose cross-mode rows via unqualified `FROM applications` reads — cross-mode data is only reachable through the qualified `real.applications` alias, which no code path in `jscc/` uses.
- **Missing marker / corrupt marker mode tests (M2).** Already landed in A6 (moved forward from A7 during hardening).
- **L5 nit.** Removed unused `connect`/`init_db` imports from `cli.py` — dead code that was also a safety-surface smell.
- 16 new pytest cases (126 total): 3 scanner (US-parenthesized, dotted international, digits-only), 12 CLI, 1 ATTACH.

### A6 — Phase A hardening (adversarial review findings)
Fixes six CRITICAL and one HIGH finding from the Phase A → B adversarial review. All safety-relevant; landed before Phase B introduces the first LLM call so nothing downstream inherits a weak guarantee.

- **Sanitizer authenticity (C1/C2/C3).** `sanitize_for_llm` now returns a frozen `SanitizedPayload` dataclass carrying an HMAC-SHA256 authenticator over stable-JSON(data) + sanitized_at, keyed by a per-process 32-byte secret generated at import via `secrets.token_bytes`. `verify()` recomputes with constant-time compare. Forged wrappers, mutated data, and swapped timestamps all fail verify. `contains_personal` refusal now uses `bool(...)` — catches `1`/`"true"`/`"yes"` and any other truthy sentinel. `SanitizerRefusal` now inherits from `Exception` (was `ValueError`) so a generic `except ValueError:` in an upstream builder cannot silently swallow refusals. Removed `is_sanitized(dict)` — the wrapper + `verify()` replace it. ADR-005 documents alternatives.
- **`open_for_mode` ordering (C4).** Full DDL used to run BEFORE the mode marker was checked, so a wrong-mode open would `PRAGMA user_version` on the wrong file before refusing. Reordered to: create only the `meta` table, detect whether the DB has any user tables, then branch — fresh DB (no user tables) runs full init + stamps; populated DB verifies the marker matches BEFORE any DDL runs.
- **Missing-marker refuse (C5).** A populated DB whose `meta.mode` row is missing now raises `ModeMismatchError` rather than silently restamping under the caller's mode. Only truly empty DBs get a fresh stamp.
- **Corrupt-marker refuse (C6).** A tampered marker value (e.g. `'production'`) now raises `ModeMismatchError` instead of a bare `ValueError` that would slip past `except ModeMismatchError:` guards. Connection is always closed before raising via a `try/except BaseException` guard.
- **`_dump_json` robustness (H4).** New `_json_default` fallback handles the value types Phase B extraction is likely to embed in `extracted_jd`: `datetime` → UTC ISO-8601, `date` → ISO, pydantic `BaseModel` → `model_dump(mode='json')`, `Enum` → `.value`, `set`/`frozenset` → sorted list. Unknown types raise `TypeError` with a clear message rather than silent swallow.
- 24 new pytest cases (110 total): 14 sanitizer (wrapper roundtrip, verify true/false paths, forgery attempts, data/timestamp tampering, truthy-refusal parametrized, `SanitizerRefusal` not caught by `except ValueError`, defensive-copy, frozen-dataclass); 4 mode (missing-marker refused, corrupt-marker raises `ModeMismatchError`, no-DDL-on-wrong-mode, meta-only DB treated as fresh); 3 storage (extracted_jd with datetime+set+enum, with nested pydantic model, with unknown type raising `TypeError`).
- ADR-005 documents the sanitizer authenticity design with rejected alternatives (isinstance-only, module-visibility, marker-only, dict subclass, persistent secret, OTP registry).

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
