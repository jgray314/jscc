# ADR 003: two-instance data mode isolation (synthetic vs. real)

**Status:** Accepted (2026-08-28, Slice A4.5a)

## Context

JSCC is a personal, single-user tool that will eventually hold real job-search data — companies, roles, contacts, notes. Per D7 (dual-use safety architecture) and D8 (no personal identity in LLM traffic), the tool must make it structurally impossible to mix the public synthetic demo fixture with private real data. Discipline alone ("just remember to point at the right file") is not enough — a wrong point-and-click at demo time would leak real data.

## Decision

Two mutually exclusive modes, `synthetic` and `real`, selected via the `JSCC_DATA` environment variable (default `synthetic`). Each mode is mapped to a distinct DB file (`data/synthetic.db` vs. `data/real.db`), and each DB carries a marker inside it (`meta.mode`) that is verified on every open. A mismatch raises `ModeMismatchError` and closes the connection before any read.

Three layers of enforcement, in order of trust:

1. **Path convention** (weakest): `data/<mode>.db`. Easy to intuit; easy to override.
2. **Env-var routing** (medium): `JSCC_DATA` drives which path is opened. Overrides live only in tests via monkeypatched env or direct `connect()` calls.
3. **In-DB marker** (strongest): the `meta` table row `('mode', <mode>)` is stamped at first init and verified on every subsequent open. This survives file moves, copies, and misconfigured env vars.

The `seed synthetic` command carries an additional guard: it refuses to run when `JSCC_DATA=real`, since real data is not something to overwrite with a synthetic fixture regardless of paths.

## Alternatives considered

- **Single DB file with a `mode` column on every row.** Fewer files to manage, but every query becomes conditional and a missing `WHERE mode=?` filter silently leaks data across modes. The point of the safety architecture is that "forgot to filter" should not be a data-loss vector. Rejected.
- **Path convention only, no marker.** Simpler, but a `mv` or `cp` between paths silently reassigns the data. The marker adds one round-trip on open in exchange for closing the copy/move loophole. Kept.
- **Hard refuse-to-read-real** (real mode locked entirely until Phase B ships). Rejected because it delays the safety contract's testability — I want the mode-flip / mismatch enforcement working now, so later slices can rely on it without a scramble.
- **A `.env` file per mode instead of an env var.** Rejected — an env var makes the active mode visible in the shell prompt (via customization) and prevents accidental commit of a real-mode config.

## Consequences

- Positive: mode-crossing is a structural error, not a discipline error. Reviewer signal.
- Positive: the marker survives file operations that path-only enforcement would miss.
- Positive: tests still get an escape hatch via raw `connect(path)`, which is fine because tests are not the vector we're protecting against.
- Cost: schema bump to v2 to introduce the `meta` table.
- Cost: every user-facing CLI now has two entry points (`db init`, `seed`, `report`) each of which opens through `open_for_mode` and needs consistent error handling. Kept the wiring in `cli.py` in one place.
- Revisit if: a legitimate multi-tenant use case ever arrives (unlikely for a personal tool) or if the check overhead becomes measurable (single-digit microseconds, so effectively never).

## Related

- Follow-on Slice A4.5b introduces `M3` (pre-commit content controls) and `M5` (sanitizer skeleton). The mode isolation here is a precondition — the pre-commit hook needs to know where real data lives to know what to scan for.
- Rule 0 in the parent plan (privacy/dual-use safety is non-negotiable across all three portfolio projects).
