# JSCC — Job Search Command Center

[![ci](https://github.com/jgray314/jscc/actions/workflows/ci.yml/badge.svg)](https://github.com/jgray314/jscc/actions/workflows/ci.yml)

A pipeline tracker for a real job search. Today it fetches and ingests job descriptions through an eval-backed LLM extraction stage, scores fit against a profile through a second eval-backed LLM stage, stores them, and surfaces stale opportunities. A follow-up drafter first routes each case as routine or non-routine, drafts only the routine ones, and hands everything else to a person as a briefing card. Both drafter prompts have been checked against real model output; how far that evidence goes is in [Status](#status).

Part of the [ai-portfolio](https://github.com/jgray314/ai-portfolio) index. **Phase F (narrative) closed 2026-09-27** — the last phase in the original plan, so this is functionally v1. See [CHANGELOG.md](CHANGELOG.md) for the slice-by-slice arc, and [docs/technical-reference.md](docs/technical-reference.md#status) for the phase-by-phase detail.

## Contents

- [Start here](#start-here-six-things-worth-reading-first)
- [Why this project](#why-this-project)
- [Quick start](#quick-start)
- [Running the dashboard](#running-the-dashboard)
- [Sample output](#sample-output)
- [Sample drafter output](#sample-drafter-output)
- [Architecture](#architecture)
- [ADRs](#adrs)
- [Status](#status)
- [License](#license)

Two companion docs go deeper than this README does: [docs/how-i-built-this.md](docs/how-i-built-this.md) (the narrative — why it's shaped this way, what the eval rounds showed, what building it with an AI agent looked like) and [docs/technical-reference.md](docs/technical-reference.md) (architecture diagram, repo layout, development commands, full phase-by-phase status).

## Start here: six things worth reading first

If you have ten minutes, these are the parts of the repo that show the most, in the order I'd read them. Each links to the code or the write-up, not just a claim.

1. **Eval results reported with the rounds that produced them.** Extraction, scoring, routing and composition each ship behind an eval suite whose bar lives in code, with every round — including the ones that failed — on the record. → [evals/README.md](evals/README.md) (every round, case sizing, per-field grading rules), [`jscc/evals.py`](jscc/evals.py) (the harness, `PASS_THRESHOLD` in code).
2. **A gate finding that overturned an earlier call.** A later review found a DNS-rebinding path in the URL fetcher that an earlier review had rated low-risk and closed with a note. It was fixed in code by pinning each request to the addresses already validated. → [`jscc/fetcher.py`](jscc/fetcher.py) (`_pinned_resolution`), and the "Phase C -> D gate" entry in [CHANGELOG.md](CHANGELOG.md). The process behind it, six worked findings and where it falls short: [docs/gate-reviews.md](docs/gate-reviews.md).
3. **Safety by construction, not discipline.** One definition of "personal data" enforced at two egress points, git and every LLM call, with authenticated payloads and a single call path to the model so no caller can skip redaction. → [`jscc/personal_data.py`](jscc/personal_data.py), [`jscc/sanitizer.py`](jscc/sanitizer.py), [`jscc/stage_call.py`](jscc/stage_call.py), [ADR-005](decisions/005-sanitizer-authenticity.md), [D7 and D8](docs/design-principles.md#d7--dual-use-data-safety-structural-not-disciplinary). The one-page [threat model](docs/threat-model.md) lists thirteen threats with the control, the residual and an honest status for each, including the ones still open.
4. **Decisions with the rejected alternatives written down.** Seven ADRs and ten design principles, including what was dropped (RAG in the drafter, a hosted demo, a multi-agent orchestrator) and why. → [decisions/](decisions/), [docs/design-principles.md](docs/design-principles.md).
5. **Cost and time reported with their limits.** LLM calls are metered at the call site and `jscc costs` reports them; no real dollar figures exist yet, and the [Cost envelope](docs/technical-reference.md#status) says so. A separate script estimates active working time from commit timestamps and prints its own biases next to the number. → [`jscc/instrumentation.py`](jscc/instrumentation.py), [`jscc/cost_report.py`](jscc/cost_report.py), [`scripts/active_time.py`](scripts/active_time.py).
6. **The process of building this with an AI agent, not just the artifact.** 100+ slices across six phases, each with its own plan/execute/validate cycle and a context-compaction handoff at every slice boundary. It also includes a correction made in the open: an in-the-moment impression that gate and hardening work was "the other 80%" of the effort didn't survive being checked against commit timestamps — the real split was closer to 48/52. → [docs/how-i-built-this.md](docs/how-i-built-this.md), [docs/lessons-learned.md](docs/lessons-learned.md), [`scripts/active_time.py`](scripts/active_time.py).

## Why this project

Three ideas being demonstrated at once — the full story, including what the eval rounds actually showed, is in [docs/how-i-built-this.md](docs/how-i-built-this.md):

1. **Eval-driven agent design.** Every LLM stage ships behind an eval suite whose bar lives in code, not in prose. No `ANTHROPIC_API_KEY` is configured for this project, so each prompt was checked by hand — completions captured from real model output and replayed through the harness's `--record`/`--replay` fixtures. Current published figures: extraction 33/38 (87%), fit scoring 29/30 (97%), routing 38/39 (97%), composition 25/28 (89%) — none of them a held-out rate. Full detail: [evals/README.md](evals/README.md).
2. **Structural safety for dual-use data.** The tool runs against real personal data and against a synthetic fixture. Safety is enforced by construction, not by user discipline — two isolated DBs stamped with a mode marker, and two egress points that share one definition of "personal": a pre-commit scanner guarding git, and an authenticated, redacting sanitizer guarding every LLM call ([D7](docs/design-principles.md#d7--dual-use-data-safety-structural-not-disciplinary), [D8](docs/design-principles.md#d8--hard-line-on-personal-identity-in-llm-traffic)).
3. **Knowing when not to automate.** The drafter routes anything non-routine to a briefing card rather than a prose draft ([D10](docs/design-principles.md#d10--drafter-routing-first-routine-only-composition)) — a routing classifier with a zero-tolerance gate on the one failure that matters (auto-drafting something that needed a human), a rule that sends any reply needing an unrecorded fact to a person, and a composer that can decline for the same reason. The drafter never sends anything; its output is a draft or a card for a person to act on.

## Quick start

```bash
uv sync
uv run python -m jscc validate-config
uv run python -m jscc db init
uv run python -m jscc seed --random-seed 42 --now 2026-08-28T12:00:00+00:00
uv run python -m jscc report --now 2026-08-28T12:00:00+00:00
```

Default mode is `synthetic`. Switch by env: `JSCC_DATA=real`. Neither DB is tracked — the synthetic one is regenerated by the `seed` command above, deterministically, which is why the sample output below reproduces.

## Running the dashboard

The dashboard (`jscc/web/`, ADR-007) is the same read/write logic as the CLI, rendered over HTTP. Same mode rules as everything else: the `JSCC_DATA` env var picks synthetic vs. real, and the SYNTHETIC MODE banner in the page header tells you which one you're looking at.

```bash
uv sync
uv run jscc db init
uv run jscc seed --random-seed 42
uv run jscc serve --port 8000
```

Then open http://127.0.0.1:8000. No `ANTHROPIC_API_KEY` is required for browsing. Running it against real data, the bind-address and request-guard details, and what the DLQ resolve form does without a key are in [docs/technical-reference.md](docs/technical-reference.md#running-the-dashboard-in-production).

## Sample output

No hosted demo exists (D4 — video is the planned demo for v1, not a live deployment) and the video itself hasn't shipped yet (Phase F, slice F2). Until then, this section and "Sample drafter output" below are the demo: real commands against the seeded fixture, output reproduced verbatim.

`uv run python -m jscc report --now 2026-08-28T12:00:00+00:00` after the seed above, reproduced verbatim:

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
  identified        Meridian Payments  Engineering Manager, Growth  overdue by 4d (last interaction 11d ago, threshold 7d)
  recruiter_screen  Ceres Analytics    Director of Engineering, ML  overdue by 3d (last interaction 10d ago, threshold 7d)
  identified        Gale Networks      Engineering Manager, Reliability  overdue by 3d (last interaction 10d ago, threshold 7d)
  hm_screen         Xenon Retail       Staff MLE, Foundations  overdue by 2d (last interaction 9d ago, threshold 7d)
  applied           Falcon Ledger      Staff Software Engineer, Infra  overdue by 1d (last interaction 15d ago, threshold 14d)
  applied           Orbit Media        Staff MLE, Foundations  overdue by 1d (last interaction 15d ago, threshold 14d)
  identified        Quartz Signals     Engineering Manager, Reliability  overdue by 1d (last interaction 8d ago, threshold 7d)
  applied           Bluewave Systems   Staff MLE, Foundations  overdue by 0d (last interaction 14d ago, threshold 14d)
  identified        Juno Labs          Engineering Manager, Platform  overdue by 0d (last interaction 7d ago, threshold 7d)
```

Bit-reproducible, but only because **both** halves are pinned. Funnel counts depend on stored state alone. The stale block is measured against a reference instant, so `report` takes `--now` as well as `seed` — read against the wall clock instead, the same database drifts by a day per day and the block above stops matching tomorrow.

## Sample drafter output

`followup` prints one of two things. These were produced by replaying recorded real-model responses (Haiku for routing, Sonnet 5 for composition) for two eval fixtures through the same renderers the command uses. Offline, against the stub clients, `followup` on the seeded database always prints a briefing card, because the stub router answers non-routine.

A routine situation (the history records a full-loop onsite) gets a draft:

```
Subject: Thank you for the onsite

Thank you all for the time on Thursday. I really enjoyed the full loop, from the system design session to the two behavioral rounds and the panel with the skip-level.

The conversations left me even more interested in the Engineering Manager, Platform role. It was great to get a real sense of how the team thinks about the work, and I appreciate everyone's openness throughout the day.

Please pass along my thanks to the whole panel. Happy to answer any follow-up questions as you move ahead.

Best,
```

A non-routine one (the candidate has decided to withdraw) gets a card and no draft:

```
HANDLE MANUALLY -- Cedar Ridge Analytics: Engineering Manager
Stage: closed
Why: Declining an application affects the relationship with the recruiter and company, and how the candidate communicates this decision shapes whether that door stays open for future opportunities.
Weigh before replying:
  - Tone and wording should feel gracious and respectful, not dismissive of the role or company
  - How to frame the scope mismatch reason without sounding like the candidate is backing away or hesitating
  - Whether to signal openness to future opportunities or roles at the company
  - How much detail to include about why the role no longer fits, given the candidate's other conversations have progressed further
Application: app-26
```

## Architecture

Every LLM stage reaches the model through one path (`stage_call.py`), so no caller can skip the sanitizer or duck the cost meter — deliberate, per [D7](docs/design-principles.md#d7--dual-use-data-safety-structural-not-disciplinary). Storage is a mode-stamped SQLite layer shared by the CLI and the dashboard, so the two surfaces can't disagree on what's stale. Full diagram and module-by-module detail: [docs/technical-reference.md](docs/technical-reference.md#architecture).

## ADRs

Design decisions with rejected alternatives:

- [ADR-001 — pydantic vs. jsonschema](decisions/001-pydantic-vs-jsonschema.md)
- [ADR-002 — stdlib sqlite3](decisions/002-stdlib-sqlite3.md)
- [ADR-003 — mode isolation via stamped marker](decisions/003-mode-isolation.md)
- [ADR-004 — pre-commit.com framework + local Python hook](decisions/004-precommit-framework.md)
- [ADR-005 — sanitizer authenticity via HMAC wrapper](decisions/005-sanitizer-authenticity.md)
- [ADR-006 — eval bars, manual capture, and deterministic grading](decisions/006-eval-bars-and-manual-capture.md)
- [ADR-007 — dashboard web stack: FastAPI + Jinja2](decisions/007-web-stack-choice.md)

The ten locked design principles behind them are in [docs/design-principles.md](docs/design-principles.md).

## Status

**Read [CHANGELOG.md](CHANGELOG.md) for the arc, [docs/technical-reference.md](docs/technical-reference.md#status) for the phase-by-phase table, gates, and cost envelope.** Current headline numbers:

- **Eval pass rates:** extraction 33/38 (87%), fit scoring 29/30 (97%), routing 38/39 (97%), composition 25/28 (89%). None is a held-out rate — every round, miss, and threat to validity is in [evals/README.md](evals/README.md).
- **788 pytest cases.** Lint and format enforced via ruff.
- **No real dollar figures yet** — no `ANTHROPIC_API_KEY` is configured, so every model call so far ran against a stub client or was captured by hand through Claude.ai chat. The cost-transparency machinery (D5) is built and tested; what it reports on real billing waits on a live key.
- **Every phase gate-closed**, most recently Phase F (narrative) on 2026-09-27, including a same-day full-project gate that found and fixed one High (a prefix-collision bug that silently defeated contact-name redaction). Two open, deliberately decoupled personal-cadence items remain: the video walkthrough (F2) and the blog post revision/publish (F3b). **Post-v1 work since Phase F closed:** a confirm-before-create step for a mismatched extracted title/company (with a same-day Critical trust-boundary fix, and a second such fix from this gate — see [docs/gate-reviews.md](docs/gate-reviews.md)), and a fifth eval suite (`evals/extraction_to_scoring`) measuring how far a real extraction error moves a downstream fit score.

## License

MIT — see [LICENSE](LICENSE).
