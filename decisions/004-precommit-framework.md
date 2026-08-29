# ADR 004: pre-commit framework choice — `pre-commit.com` + local Python hook

**Status:** Accepted (2026-08-28, Slice A4.5b)

## Context

D7 M3 requires content-level enforcement: personal-data-shaped strings must not
enter the public repo, even by accident. The check has to run at commit time
(after that it's too late — a `git push` publishes it), and it needs to be
reproducible for a reviewer inspecting the repo. It also needs to be *skippable
in tests* — the scanner script itself has to be exercisable directly, without
requiring reviewers to install the pre-commit framework to run pytest.

## Decision

Use `pre-commit.com` as the framework, wired to a **local** hook that shells out
to `scripts/precommit_scan.py`. The script is a plain Python module: standalone,
argparse-driven, and importable — so `pytest tests/test_precommit_scan.py` runs
the exact same code path that `pre-commit run` invokes, with no framework
dependency.

Enforcement layers:

1. `.pre-commit-config.yaml` (framework wiring). Contributor runs
   `pre-commit install` once; the hook fires on every `git commit`.
2. `scripts/precommit_scan.py` (the actual scanner). Regex rules for email +
   phone; substring rules from `.safety/danger-list.txt`.
3. `.safety/danger-list.txt` (committed scaffold with comments only). Real
   personal identifiers go in a **local** danger list — the committed one
   stays as a template so the repo doesn't itself leak the terms it's
   guarding against.

## Alternatives considered

- **Native git hook script (`.git/hooks/pre-commit`)**. Zero framework
  dependency, but `.git/hooks/` is not tracked — reviewers can't see it, new
  clones lose it, and there's no `pre-commit run --all-files` for CI. The
  point of this control is *visibility of the guardrail*, not just its
  presence. Rejected.
- **Pure GitHub Actions check on push**. Runs too late — the moment `git push`
  succeeds, the content is on GitHub even if the check fails. Kept as a
  possible *belt-and-braces* addition later, but not the primary control.
- **`husky` / Node-based hook manager**. JSCC has no Node in its toolchain;
  adding Node just for the hook is a large dependency for a small check.
  Rejected.
- **Inline the scan into a git commit-msg or prepare-commit-msg hook** rather
  than pre-commit. Wrong stage — those hooks run after staged content is
  finalized; pre-commit is the earliest hook that sees the staged diff.
  Rejected.
- **Larger regex battery (SSN, credit cards, DOB, passport patterns).**
  Deferred to Phase B — the email/phone/danger-list trio covers the current
  attack surface for JSCC data. Adding rules with no observed miss creates
  false-positive load without safety gain.

## Consequences

- Positive: hook config is source-controlled and reviewable; the scanner is
  testable directly; committed danger list stays a template.
- Positive: the ISO-date false positive (dates like `2026-08-28` matching the
  raw phone regex) is defused by a digit-count secondary filter — real phone
  numbers have 10-15 digits; ISO dates have 8. This came out of the A4.5b
  dry-run against tracked files. A test case pins the behavior.
- Cost: contributors need to run `pre-commit install` once. A future CI job
  running `pre-commit run --all-files` closes the "forgot to install" gap;
  deferred to when CI shows up.
- Cost: the local Python hook incurs a Python startup per commit. Acceptable
  for a personal-use tool; not on any hot path.
- Revisit if: the false-positive rate on real notes becomes annoying (tighten
  regex or narrow file types) or if a real leak slips through (add rules).

## Related

- ADR-003 (mode isolation) establishes *where* real data lives; this ADR
  establishes *what content pattern* is disallowed anywhere in tracked files.
- D7 M5 (sanitizer) is the runtime counterpart — this hook prevents the
  identifiers from entering the repo; the sanitizer prevents them from
  leaving the process to an LLM. Skeleton landed alongside this ADR;
  substantive rules come with Phase B.
