# JSCC — Job Search Command Center

[![ci](https://github.com/jgray314/jscc/actions/workflows/ci.yml/badge.svg)](https://github.com/jgray314/jscc/actions/workflows/ci.yml)

A pipeline tracker for a real job search. Ingests job descriptions, scores fit against a profile, drafts follow-ups for routine cases only, and surfaces stale opportunities. The eval-gated LLM stages ship in Phase B.

Part of the [ai-portfolio](https://github.com/jgray314/ai-portfolio) index. Phase A (foundations) is complete; Phase B (evals + first LLM stage) is next. See [CHANGELOG.md](CHANGELOG.md) for the slice-by-slice arc.

## Why this project

Three ideas being demonstrated at once:

1. **Eval-driven agent design.** Every LLM stage is behind an eval suite. The extract / score split ([D9](docs/design-principles.md#d9--llm-stages-are-split-extract--score-scorer-sees-raw-jd-too)) exists so extraction facts and scoring judgment can regress independently.
2. **Structural safety for dual-use data.** The tool runs against real personal data and against a synthetic fixture. Safety is enforced by construction, not by user discipline — two isolated DBs stamped with a mode marker, a pre-commit scanner, and an authenticated sanitizer wrapper on every LLM call ([D7](docs/design-principles.md#d7--dual-use-data-safety-structural-not-disciplinary), [D8](docs/design-principles.md#d8--hard-line-on-personal-identity-in-llm-traffic)). *Phase A ships the wrapper + HMAC integrity + `send_to_llm` boundary; content redaction rules attach at `_transform` in Phase B.*
3. **Knowing when not to automate.** The drafter routes to a briefing card, not a prose draft, for anything non-routine ([D10](docs/design-principles.md#d10--drafter-routing-first-routine-only-composition)).

## Quick start

```bash
uv sync
uv run python -m jscc validate-config
uv run python -m jscc db init
uv run python -m jscc seed --random-seed 42 --now 2026-08-28T12:00:00+00:00
uv run python -m jscc report
```

Default mode is `synthetic`. Switch by env: `JSCC_DATA=real`. The real DB (`data/real.db`) is gitignored; the synthetic one is tracked as a portfolio-visible fixture.

## Sample output

`uv run python -m jscc report` after the seed above:

```
[mode: synthetic]
Funnel
------
  identified           6
  applied              8
  recruiter_screen     3
  hm_screen            3
  technical_loop       2
  onsite               1
  offer                0
  closed               2
  (total)             25

Stale alerts (13)
----------------
  hm_screen         Yield Model Co     Director of Engineering, ML  overdue by 25d (last interaction 32d ago, threshold 7d)
  applied           Rift Cloud         Director of Engineering, ML  overdue by 19d (last interaction 33d ago, threshold 14d)
  identified        Timber Motors      Director of Engineering, ML  overdue by 13d (last interaction 20d ago, threshold 7d)
  applied           Pinnacle Search    Director of Engineering, ML  overdue by 7d (last interaction 21d ago, threshold 14d)
  ...
```

Bit-reproducible for a pinned `--random-seed` and `--now`.

## Repo layout

```
jscc/           library code
  config.py     load + validate stages.yaml, profile.yaml
  mode.py       synthetic/real mode resolution + DB path convention
  storage.py    SQLite persistence with stamped mode marker
  models.py     pydantic domain models (Application, Contact, Interaction, ...)
  seed.py       deterministic synthetic fixture (evaluation infrastructure)
  sanitizer.py  the LLM-egress choke point; HMAC-wrapped payloads
  report.py     staleness detector + funnel counts
  cli.py        click entry point
tests/          pytest suite (142 tests)
config/         stages.yaml + profile.example.yaml
scripts/        pre-commit content scanner (danger-list + email/phone regex)
decisions/      ADRs (see below)
docs/           design-principles.md
.github/        CI workflow
data/           synthetic.db (tracked); real.db (gitignored)
```

## ADRs

Design decisions with rejected alternatives:

- [ADR-001 — pydantic vs. jsonschema](decisions/001-pydantic-vs-jsonschema.md)
- [ADR-002 — stdlib sqlite3](decisions/002-stdlib-sqlite3.md)
- [ADR-003 — mode isolation via stamped marker](decisions/003-mode-isolation.md)
- [ADR-004 — pre-commit.com framework + local Python hook](decisions/004-precommit-framework.md)
- [ADR-005 — sanitizer authenticity via HMAC wrapper](decisions/005-sanitizer-authenticity.md)

The ten locked design principles behind them are in [docs/design-principles.md](docs/design-principles.md).

## Development

```bash
uv sync
uv run pytest              # ~seconds
uv run pre-commit install  # enable the safety scanner
```

The pre-commit scanner refuses commits that match name/email/phone patterns or entries in a local `.safety/danger-list.local.txt` (gitignored).

## Status

Phase A hardening complete: three rounds of adversarial + reviewer-walkthrough gates, structural fixes for every critical + high finding, 142 pytest cases, 5 ADRs. Phase B starts the evals + first LLM stage.

## License

MIT — see [LICENSE](LICENSE).
