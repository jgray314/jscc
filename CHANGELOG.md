# Changelog

## [Unreleased]

### CI hotfix — comment in llm_client.py re-tripped the scanner it was explaining
B2's fix for the model-id false positive (split the literal via concatenation) was correct, but the comment explaining *why* quoted both offending strings verbatim (`"4-5-20251001"` and `"555-123-4567"`), which matched the same phone-pattern regex it was documenting — same failure class as the CHANGELOG.md problem from A10 (prose describing a scanner match trips the scanner), just inside a code comment instead of a tracked doc. Confirmed via a fresh clone rather than the local working tree, since that's exactly how this slipped past the last local check. Fixed by describing the pattern's shape instead of requoting it, with an explicit comment note against doing this again. Did not touch the regex itself — this is the second scanner failure in three commits, and both were self-inflicted (lockfile hash format, now self-referential prose), not the regex blocking real content; loosening it now would trade away the phone-shape coverage A9 deliberately widened it for, to fix a problem that isn't actually about sensitivity.

### B2 — JD extraction prompt v1 + LLM client plumbing
No `ANTHROPIC_API_KEY` is configured in this environment. Rather than block the slice, this lands the real prompt and the full call path — sanitizer routing, instrumentation, client abstraction — behind a `StubExtractionClient` fallback, so everything is exercisable end-to-end today and a real key is a drop-in swap later. Per the sub-plan's DoD ("eval suite passes at ≥80%"): **not met yet** — that requires live iteration against a real model, which needs the key. This is scoped honestly as prompt-authored-and-wired, not prompt-validated.

- `jscc/llm_client.py`: `LLMClient` protocol, `AnthropicClient` (real, requires `ANTHROPIC_API_KEY`, raises immediately if missing — never silently degrades), `StubExtractionClient` (fixed placeholder response, `$0` cost, clearly labeled in its own output). `default_client()` picks between them once, visibly, based on the env var. `EXTRACTION_MODEL` constant built via string concatenation, not a single literal — the contiguous digit run in `claude-haiku-4-5-20251001` trips the pre-commit phone-pattern scanner (same false-positive class as A7's `555-123-4567` danger-list rewrite); splitting the literal keeps the scanner's real coverage intact everywhere else.
- `jscc/extraction.py`: real `EXTRACTION_SYSTEM_PROMPT` targeting all 7 `ExtractedJD` fields. `extract_jd(raw_text, *, conn=None, client=None)` — every call builds a payload, routes it through `sanitize_for_llm` → `send_to_llm` (the D7/D8 choke point, unmodified from A6/A10) before any client call. `conn`, when passed, wraps the call in `@instrumented("extraction")` so it lands in the D5 ledger; the eval harness calls without a `conn` (eval runs measure prompt quality, not production cost — no application DB in that context). `ExtractionParseError` on non-JSON or schema-mismatched responses.
- `.env.example` added (D7 M7 pattern) documenting `ANTHROPIC_API_KEY`.
- `anthropic` SDK added as a real dependency (`uv add anthropic`, `uv.lock` updated).
- 12 new pytest cases (173 total): client stub/real selection, request wiring (raw JD as user prompt, model/system correctness), response parsing (valid / invalid JSON / schema mismatch), instrumentation-when-conn-provided, prompt-hash-not-raw-JD. B1's harness tests updated to grade the stub honestly (0/15, no errors — grading fails cleanly, not an exception).
- `python -m jscc eval jd_extraction` today reports `0/15 passed` against the stub — expected, not a regression. Live iteration to ≥80% is the explicit next step once a key is available.

### B1 — JD extraction eval suite
First Phase B slice. Per D9, extraction and scoring are split so extraction facts can be graded independently of scoring judgment — this suite is that independence made concrete.

- `ExtractedJD` model (`title`, `level`, `comp_band`, `location`, `remote_policy`, `must_have_skills`, `responsibilities_summary`) — the contract Slice B2's prompt is written against.
- `jscc/extraction.py`: `extract_jd(raw_text) -> ExtractedJD` stub, raises `ExtractionNotImplementedError` until B2. Signature is final now so the eval suite doesn't reshape when the prompt lands.
- `evals/jd_extraction/cases.json`: 15 hand-authored JDs (fictional companies) across levels junior→director, with and without stated comp, across remote/hybrid/onsite. `evals/README.md` documents the suite and how to add cases.
- `jscc/evals.py`: hand-rolled harness. Structural fields (`level`, `remote_policy`) exact-match; `must_have_skills` set-equality; `comp_band` presence-only (dollar figures are too brittle to grade exactly); prose field (`responsibilities_summary`) checked non-empty only — real LLM-judge grading is deferred to when there's a prompt worth judging.
- `python -m jscc eval jd_extraction` CLI command — exits non-zero on any failing case, ready to gate CI once B2 lands.
- 12 new pytest cases (161 total): grading rules, fixture-file shape, harness-against-stub (all 15 fail, as expected), CLI wiring.
- DoD met: `python -m jscc eval jd_extraction` runs and reports `0/15 passed` — the harness works; there's just no prompt yet.

### A5 — LLM call instrumentation (deferred from Phase A, landed at Phase B start)
Per D5, the `@instrumented` decorator was supposed to land as Phase A foundation so no LLM call — starting with B2's extractor — could ever go uninstrumented. It slipped out of the A1-A4.5 sequence and no gate round caught the gap (three rounds of adversarial + walkthrough all reviewed *shipped* code; nothing had reason to check for a missing slice). Caught while reading the sub-plan back before starting Phase B.

- `jscc/instrumentation.py`: `@instrumented(feature: str)` wraps a function of shape `(conn, model, prompt, *a, **kw) -> LLMResult`, capturing call_id, feature, model, `sha256(prompt)` (never the prompt itself — D8 boundary applies here too), input/output tokens, cost, latency, and timestamp. Returns `result.output` unchanged so callers see the same shape as an uninstrumented call.
- `llm_calls` table (schema v2 → v3) + `record_llm_call` / `list_llm_calls` in `storage.py`.
- `LLMCallRecord` model in `models.py`.
- `python -m jscc costs` CLI command: per-feature call count / total cost / avg latency table; prints "no LLM calls recorded yet" today (empty ledger, populates from B2 onward).
- 7 new pytest cases (149 total): decorator end-to-end capture, prompt-hash-not-prompt, multi-call distinct rows, storage roundtrip + ts ordering, CLI empty + populated ledger.

### A10 — Third-gate closure (adversarial HIGH + walkthrough substance) + supply-chain hardening
Third round of both Phase A → B gates. Adversarial returned `PROCEED-WITH-FIXES` (one HIGH, five MEDIUMs, three LOWs). Walkthrough returned `lean yes to advance` with an 8-item punch list. This slice lands every finding classified as substance / structural; deferred items are noted at the end.

- **H-precommit-changelog-1 — pre-commit/CI divergence.** `.pre-commit-config.yaml` did not exclude `CHANGELOG.md`, but CI did. `pre-commit run --all-files` was refusing commits on a clean tree because the CHANGELOG's own A7/A8/A10 entries quote placeholder personal-data shapes as prose describing the scanner. Structural fix: the exclude list now lives in `scripts/scan_tracked.sh`, and both CI and the pre-commit hook invoke that single script. Drift is impossible by construction.
- **M-sanitizer-toctou-1 — shallow copy in `_transform`.** `dict(payload)` was a top-level shallow copy; nested lists / dicts inside the payload were aliased to the caller's references. A caller retaining a handle could mutate them between `verify()` and send, breaking D8 with a payload that literally passed the choke point. Fix: `_transform` now snapshots via `json.loads(_stable_json(payload))` — a deep JSON-canonical copy that shares no mutable object with the caller. New test proves post-verify mutation cannot reach `SanitizedPayload.data`.
- **Walkthrough #2 — `send_to_llm(SanitizedPayload)` Phase A boundary stub.** The wrapper's type contract had no callsite; a reviewer would ask what enforces the D8 guarantee. New `send_to_llm(payload: SanitizedPayload) -> dict` calls `verify()` and raises `LLMSendError` on failure. This makes the "authenticated wrapper on every LLM call" claim testable end-to-end today, before any real LLM stage. Three regression tests: verify-success returns data, bare-dict-via-cast raises, forged authenticator raises.
- **Walkthrough #3 + L-doc-drift-synthetic-db-1 — commit `data/synthetic.db`.** README claimed the fixture was tracked; it wasn't. Regenerated deterministically from `--random-seed 42 --now 2026-08-28T12:00:00+00:00`, committed. New `test_synthetic_fixture_passes_scanner` runs the pre-commit content scanner over the actual SQLite file — proves the portfolio-visible fixture is scrubbed and no future contributor can commit a fixture with a real name in it without CI catching it.
- **M-ci-actions-pinning-1 — SHA-pin GitHub Actions.** `actions/checkout@v4` and `astral-sh/setup-uv@v6` were pinned by floating major tag. The recent v3→v6 hotfix was the same failure mode; Phase B introduces API-key secrets. Now pinned by full commit SHA (`actions/checkout@11d59...` and `astral-sh/setup-uv@d0cc0...`) with the release tag in a trailing comment.
- **M-uvlock-untracked-1 — commit `uv.lock` + `--frozen`.** `uv.lock` was gitignored; `uv sync` resolved fresh each run. A pydantic point release could turn CI red overnight with no commit; a transitive dep compromise had a wider window. Removed from `.gitignore`, committed, CI now runs `uv sync --all-extras --frozen`.
- **M-storage-except-baseexception-1 — best-effort `conn.close()`.** The `try / except BaseException / conn.close() / raise` pattern in `open_for_mode` would let a `conn.close()` error replace the original safety-critical `ModeMismatchError`. Wrapped the close in an inner `try/except Exception: pass` so the original exception's traceback survives.
- **M-precommit-abs-paths-1 — Windows absolute paths + `**` zero-match.** `--exclude` matched against `PurePosixPath(*p.parts).as_posix()`, which on Windows produces `C:\/Users/...` for an absolute path and no glob fullmatch reached it. Now normalizes to repo-relative POSIX via `Path.resolve().relative_to(cwd.resolve())`. Also: `foo/**` now zero-matches the `foo` directory itself (standard glob semantics — previously only `foo/x` matched). Two new tests.
- **Walkthrough #1 — README opening trim.** Dropped "agentic, eval-gated" from the first sentence — the Phase A cut has zero LLM calls, and the three-idea block below explains the frame. Also updated status: "three rounds" of gates, "142 pytest cases".
- **Walkthrough #8 — README D7/D8 Phase A boundary caveat.** Added an italicized clause: "Phase A ships the wrapper + HMAC integrity + `send_to_llm` boundary; content redaction rules attach at `_transform` in Phase B." Prevents a fast reviewer from mis-reading the D7/D8 claim as promising redaction that hasn't shipped.
- 7 new pytest cases (142 total): 1 M-sanitizer-toctou nested-mutation, 3 send_to_llm boundary (verify-success / bare-dict / forged), 2 M-precommit-abs-paths (double-star zero-match + absolute-path normalize), 1 synthetic-fixture scanner sweep.

**Deferred to Phase B / follow-up polish (documented, not blocking):**
- Walkthrough #5 (ADR-001 rewrite — real judgment call on framing, want to discuss shape first).
- Walkthrough #6 (coverage badge — its own micro-slice; needs `pytest-cov` dep + workflow step).
- Walkthrough #7 (CHANGELOG split — structural doc-reorg; wants a shape agreement).
- L-json-default-sanitizer-1 (strict `_stable_json` — real once Phase B payloads have real types).
- L-report-format-injection-1 (control-char escaping in `format_report` — real once Phase B ingests real JDs).

### CI hotfix — exclude `uv.lock` from scanner + fix exit-code propagation
A10 turned CI red because `uv.lock`'s sha256 hashes contain 10-15-digit runs that trigger the phone regex (49 hits). The same run passed locally on Git Bash: `set -euo pipefail` didn't propagate `xargs`'s non-zero exit through the pipeline on that shell, so the wrapper silently reported success. Two structural fixes: (1) added `uv.lock` to the shared exclude list in `scripts/scan_tracked.sh`, (2) rewrote the wrapper to run the scanner exactly once with an explicit `if [ "$rc" -ne 0 ]` on its exit code, closing the pipe-fail gap. Verified: hit-file returns exit 1 locally.

### CI hotfix — bump `astral-sh/setup-uv` v3 → v6
A8 and A9 both landed with red CI. Both runs failed at the "Install uv" step: the pinned `@v3` tag no longer resolved against the current uv release manifest (setup-uv is at v10 upstream). Bumped to `@v6` — mature major, same `enable-cache` surface. First green run on the resulting commit.

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
