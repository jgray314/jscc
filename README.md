# JSCC — Job Search Command Center

[![ci](https://github.com/jgray314/jscc/actions/workflows/ci.yml/badge.svg)](https://github.com/jgray314/jscc/actions/workflows/ci.yml)

A pipeline tracker for a real job search. Today it fetches and ingests job descriptions through an eval-backed LLM extraction stage, scores fit against a profile through a second eval-backed LLM stage, stores them, and surfaces stale opportunities. A follow-up drafter first routes each case as routine or non-routine; its routing classifier is validated against real model output, and its composition prompt is built and graded but not yet validated — see [Status](#status) for the line between the two.

Part of the [ai-portfolio](https://github.com/jgray314/ai-portfolio) index. Phase A (foundations) and Phase B (ingestion + extraction) are shipped and gate-closed; Phase C (fit scoring) shipped and gate-closed as of 2026-09-12. Phase D (follow-up drafter) is underway — routing is validated (D2b closed, 26/26 on round 5), and the composition prompt, its deterministic grader and a `needs_input` safety net are built; manual-capture validation of composition (D4b) and the Phase D gate are next. See [CHANGELOG.md](CHANGELOG.md) for the slice-by-slice arc.

## Start here: five things worth reading first

If you have ten minutes, these are the parts of the repo that show the most, in the order I'd read them. Each links to the code or the write-up, not just a claim.

1. **An eval result reported as a band, not a point.** Extraction was measured twice on the same prompt and landed at 76% and 82%; the repo says so instead of picking one. The case count (33) is sized against the statistical noise it leaves, and the reasoning is written down. → [evals/README.md](evals/README.md) (case sizing and per-field grading rules), [`jscc/evals.py`](jscc/evals.py) (the harness, `PASS_THRESHOLD` in code).
2. **A gate finding that overturned an earlier call.** A later review found a DNS-rebinding path in the URL fetcher that an earlier review had rated low-risk and closed with a note. It was fixed in code by pinning each request to the addresses already validated. → [`jscc/fetcher.py`](jscc/fetcher.py) (`_pinned_resolution`), and the "Phase C -> D gate" entry in [CHANGELOG.md](CHANGELOG.md). The process behind it, six worked findings and where it falls short: [docs/gate-reviews.md](docs/gate-reviews.md).
3. **Safety by construction, not discipline.** One definition of "personal data" enforced at two egress points, git and every LLM call, with authenticated payloads so no caller can skip redaction. → [`jscc/personal_data.py`](jscc/personal_data.py), [`jscc/sanitizer.py`](jscc/sanitizer.py), [ADR-005](decisions/005-sanitizer-authenticity.md), [D7 and D8](docs/design-principles.md#d7--dual-use-data-safety-structural-not-disciplinary). The one-page [threat model](docs/threat-model.md) lists twelve threats with the control, the residual and an honest status for each, including the ones still open.
4. **Decisions with the rejected alternatives written down.** Five ADRs and ten design principles, including what was dropped (RAG in the drafter, a hosted demo, a multi-agent orchestrator) and why. → [decisions/](decisions/), [docs/design-principles.md](docs/design-principles.md).
5. **Cost and time reported with their limits.** LLM calls are metered at the call site and `jscc costs` reports them; no real dollar figures exist yet, and the [Cost envelope](#status) says so. A separate script estimates active working time from commit timestamps and prints its own biases next to the number. → [`jscc/instrumentation.py`](jscc/instrumentation.py), [`jscc/cost_report.py`](jscc/cost_report.py), [`scripts/active_time.py`](scripts/active_time.py).

## Why this project

Three ideas being demonstrated at once:

1. **Eval-driven agent design.** Every LLM stage ships behind an eval suite whose bar lives in code (`PASS_THRESHOLD = 0.80`), not in prose. Four stages exist, and three have cleared their bar (the fourth, drafter composition, cleared its bar on one capture round, 24/28, with a deterministic grader that does not judge tone): with no `ANTHROPIC_API_KEY` configured for this project, each prompt was validated by hand — completions captured from real model output and replayed through the harness's `--record`/`--replay` fixtures. Extraction landed at 76%–82% across two independent capture rounds on a 33-case suite; fit scoring landed at 84% on one capture round on a 25-case suite, with one deferred finding (see [Status](#status)); routing landed at 26/26 on its fifth round, after round 4 failed on a false-routine that proxy runs had missed. None of the suites is a CI gate, for the same reason: without live traffic there's nothing for CI to run against beyond the fixed recording, so gating today would gate on a frozen fixture rather than the prompt. The extract / score split ([D9](docs/design-principles.md#d9--llm-stages-are-split-extract--score-scorer-sees-raw-jd-too)) exists so extraction facts and scoring judgment can regress independently.
2. **Structural safety for dual-use data.** The tool runs against real personal data and against a synthetic fixture. Safety is enforced by construction, not by user discipline — two isolated DBs stamped with a mode marker, and two egress points that share one definition of "personal": a pre-commit scanner guarding git, and an authenticated, redacting sanitizer guarding every LLM call ([D7](docs/design-principles.md#d7--dual-use-data-safety-structural-not-disciplinary), [D8](docs/design-principles.md#d8--hard-line-on-personal-identity-in-llm-traffic)). Redaction is unconditional and runs *before* the payload is authenticated, so no caller can opt out of it — including the ones that forget to. The guarantee's scope is stated narrowly and honestly in D8: structured identifiers and known names, not free-text NER.
3. **Knowing when not to automate.** The drafter routes anything non-routine to a briefing card rather than a prose draft ([D10](docs/design-principles.md#d10--drafter-routing-first-routine-only-composition)). Deciding where automation stops is the design work, and it is built: a routing classifier with a zero-tolerance gate on the one failure that matters (auto-drafting something that needed a human), a rule that sends any reply needing an unrecorded fact to a person, and a composer that can decline for the same reason. The composition prompt itself is not yet validated against real model output (D4b).

## Quick start

```bash
uv sync
uv run python -m jscc validate-config
uv run python -m jscc db init
uv run python -m jscc seed --random-seed 42 --now 2026-08-28T12:00:00+00:00
uv run python -m jscc report --now 2026-08-28T12:00:00+00:00
```

Default mode is `synthetic`. Switch by env: `JSCC_DATA=real`. Neither DB is tracked — the synthetic one is regenerated by the `seed` command above, deterministically, which is why the sample output below reproduces.

## Sample output

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

## Repo layout

```
jscc/           library code
  config.py     load + validate stages.yaml, profile.yaml
  mode.py       synthetic/real mode resolution + DB path convention
  storage.py    SQLite persistence with stamped mode marker
  models.py     pydantic domain models (Application, Contact, Interaction, ...)
  seed.py       deterministic synthetic fixture (evaluation infrastructure)
  sanitizer.py  the LLM-egress choke point; redacts, then HMAC-wraps
  personal_data.py  one definition of "personal" — shared by the scanner + sanitizer
  json_utils.py one definition of the JSON serialization fallback — shared by storage + sanitizer
  paths.py      one definition of where this installation's files live (package-anchored, never cwd)
  instrumentation.py  @instrumented — cost/latency/token capture on every LLM call
  extraction.py the extract_jd interface (D9 step 1) + JD extraction prompt v1
  llm_client.py Anthropic client + StubExtractionClient fallback (no key configured yet)
  evals.py      hand-rolled eval harness (jd_extraction, fit_scoring, routing, composition)
  fetcher.py    guarded requests + readability JD fetcher; optional Playwright fallback for JS-heavy pages
  scoring.py    the score_fit interface (D9 step 2) + fit-scoring prompt v1
  routing.py    the route_followup interface (D10 step 1) + routing prompt v1
  composition.py  compose_followup, the composition prompt + call path (D10 step 2A, D4a)
  followup.py   briefing renderer (D10 step 2B) + the top-level followup() orchestrator
  report.py     staleness detector + funnel counts
  stage_call.py the one path from an LLM stage to the model client: sanitize, verify, meter, call
  terminal.py   strips control characters from everything a command prints
  cli/          click entry point, one module per command family: admin (validate-config, db init, seed, report, costs),
                ingest (ingest, dlq list, resolve-dlq), agents (score, route, followup), eval_cmds (eval <suite>)
tests/          pytest suite (607 tests)
config/         stages.yaml, profile.example.yaml, pipeline.yaml (playwright_fallback flag)
evals/          eval suites (jd_extraction, fit_scoring, routing, composition); evals/README.md
scripts/        pre-commit content scanner (imports its rules from jscc/personal_data.py); smoke_fetch.py (real-URL smoke test, not CI-gated); active_time.py (active-time proxy from commit gaps, prints its own bias)
decisions/      ADRs (see below)
docs/           design-principles.md; threat-model.md; gate-reviews.md; lessons-learned.md; smoke-test-results.md (smoke_fetch.py output snapshot)
.github/        CI workflow
data/           synthetic.db, real.db -- both gitignored; seed regenerates the synthetic one
```

## ADRs

Design decisions with rejected alternatives:

- [ADR-001 — pydantic vs. jsonschema](decisions/001-pydantic-vs-jsonschema.md)
- [ADR-002 — stdlib sqlite3](decisions/002-stdlib-sqlite3.md)
- [ADR-003 — mode isolation via stamped marker](decisions/003-mode-isolation.md)
- [ADR-004 — pre-commit.com framework + local Python hook](decisions/004-precommit-framework.md)
- [ADR-005 — sanitizer authenticity via HMAC wrapper](decisions/005-sanitizer-authenticity.md)

The ten locked design principles behind them are in [docs/design-principles.md](docs/design-principles.md).

What building it taught me, as a running set of lessons with what worked and what each would mean for a team: [docs/lessons-learned.md](docs/lessons-learned.md). It is an early draft and still being edited.

## Development

```bash
uv sync
uv run pytest              # ~seconds
uv run ruff check .        # lint
uv run ruff format --check .  # formatting
uv run python -m jscc eval jd_extraction --replay   # eval suite, no API key
uv run pre-commit install  # enable the safety scanner + ruff hooks
uv run playwright install chromium  # optional -- only needed to use the Playwright fetch fallback
```

The pre-commit scanner refuses commits that match email/phone patterns, an Anthropic API key, or entries in `.safety/danger-list.txt` and the gitignored `.safety/danger-list.local.txt`. It reads the same two lists, from the same package-anchored location, as the LLM sanitizer — that shared location is part of the guarantee, not an implementation detail.

Lint and format are ruff (`pyproject.toml`'s `[tool.ruff]`), enforced by the same pre-commit hooks and CI job as the content scanner. `E501` (line length) is deliberately off — this codebase's design-rationale comments and docstrings are long-form prose by design, and wrapping them at a fixed column would be churn against an established writing style, not a real improvement.

## Status

**Read [CHANGELOG.md](CHANGELOG.md) for the arc** — the slice-by-slice history, the review findings, and the reasoning behind each fix, not just the fixes themselves.

| | Shipped | Next |
|---|---|---|
| **Phase A — foundations** | Config, storage with a stamped mode marker, the sanitizer choke point, the pre-commit scanner, 5 ADRs. Closed after three gate rounds. | — |
| **Phase B — ingestion + extraction** | Eval suite, extraction prompt validated against real model output (76%–82% band, see below), fetcher + Playwright fallback + DLQ, paste-only path, three-value exit contract, ruff lint/format gate. Phase B → C gate fully closed. | — |
| **Phase C — fit scoring** | Eval suite (C1), prompt + client plumbing (C2a), and manual-capture validation (C2b — 84% on round 1, above the bar; one deferred finding, see CHANGELOG). Cost/latency reporting (C3): `jscc costs` prints per-feature cost, latency percentiles, and flags any call whose recorded cost no longer matches its model's published rate. | — |
| **Phase D — follow-up drafter** | Routing: eval suite (D1), prompt + call path (D2a) and `route` command, and manual-capture validation (D2b) — 26/26 on round 5 against the 0.85 bar with zero false-routine, after round 4 (23/26) failed on a false-routine the proxy runs had missed. Composition: eval suite (D3, 28 cases), prompt + call path (D4a), a deterministic grader that checks for mechanical defects and does not grade tone (D4b grader), and a `needs_input` safety net so the composer can decline instead of inventing a fact (D4c). Briefing renderer + `followup` orchestrator (D5). | The Phase D gate. |

**Built and shipped.** Phase A foundations: config, storage with a stamped mode marker, the sanitizer choke point, the pre-commit scanner, 5 ADRs — closed after three rounds of adversarial and reviewer-walkthrough gates with structural fixes for every critical and high finding. Phase B: B1 (eval suite), B2 (extraction prompt + client plumbing, validated against real model output — see below), B3a (baseline fetcher + DLQ core), B3b (Playwright fallback + real-URL smoke test), B4 (JD paste-only path), and B5–B9, a second two-lens gate and its closure — package-anchored safety paths, extraction failures routed to the DLQ instead of crashing, full extracted records stored rather than one field, correct response decoding, a three-value exit contract, and the eval pass-rate threshold with record/replay. Phase C: C1 (fit-scoring eval suite), C2a (real prompt + call path), C2b (manual-capture validation, 84% on round 1), and C3 (cost/latency reporting) — all shipped, then a Phase C → D gate that fixed a DNS-rebinding SSRF gap an earlier pass had rated low-risk, a missing score-range check, and a billed-but-unlogged call path. Phase D: routing end to end (D1 eval suite, D2a prompt + `route` command, D2b manual-capture validation, 26/26 on round 5), composition built and graded (D3 suite, D4a prompt + call path, D4b deterministic grader, D4c `needs_input` safety net), and D5 (non-routine briefing renderer + `followup` orchestrator, built ahead of D4).

**Validated once, with known gaps.** The composition prompt (D4a/D4c) is real and end-to-end (payload build → sanitize → send → parse, instrumented, ledger-recorded). One D4b manual-capture round on real Sonnet 4.5 output scored 24/28 (86%) against the 0.75 bar, with all 3 escalation cases correct; the 4 misses are verbatim style-sample reuse. `compose_followup` still runs against `StubCompositionClient` by default, which fails every eval case by design. The grader is deterministic and does not grade tone, so a pass means no mechanical defect, not a good email, and it does not catch invented weekdays or relative dates (seen in 3 of the 28 completions; a logged fast-follow). Routing is validated on one capture round of its current prompt (26 cases, standard error about 7 points at the 85% bar), so "26/26" is a strong result on a small suite, not a measured rate; and routing round 4 is the reason to distrust proxy-only sign-off, since the proxy runs passed a case the real chat round failed.

**B2b closed.** No `ANTHROPIC_API_KEY` is configured — this project isn't using the Anthropic Console, so extraction runs end-to-end against a stub client by default. B2b validates the prompt against real (not stub) model output captured by hand through Claude.ai chat and replayed via the eval harness's `--record`/`--replay` fixtures, rather than against live API traffic. The eval clears the ≥80% bar, but the honest number is a band, not a point figure: two independent capture rounds measured 76%–82% on the same 33-case suite holding the prompt fixed, so pass rate itself carries several points of model variance under manual capture. See CHANGELOG for the categorized breakdown — one grader gap (abbreviation pairs like "infra-as-code" vs "infrastructure as code") and a couple of documented model-consistency limits (ambiguous-title leveling; case-by-case wording variance) are left as disclosed gaps rather than chased further. Not CI-gated, since a manual-capture eval has no live traffic to gate on.

**Phase B → C gate, as of 2026-09-12: closed.** Three cold two-lens reviews (adversarial + outside-reviewer walkthrough) ran against the Phase B slices above (2026-09-01, 2026-09-04, 2026-09-12). Every finding across all three — including two highs found the same morning B2b's manual-capture eval closed (eval fixtures not pinning the extraction system prompt; a transient LLM API error crashing `ingest`/`resolve-dlq` instead of routing to the DLQ) and a low-severity backlog (duplicate-application detection, exit-code semantics for a no-op resolve, unenforced `level`/`remote_policy` vocabularies, a handful of residuals reasoned acceptable rather than fixed) — is now fixed or documented. Worked findings and how the gates run: [docs/gate-reviews.md](docs/gate-reviews.md); the full private review notes are not tracked in this repo.

**Phase C → D gate, as of 2026-09-12: closed.** A cold two-lens review ran against the Phase C slices above. The headline finding contradicted an earlier gate's own call: the fetcher's DNS-resolution guard had been rated low-risk ("reasoned not exploited") in the Phase B → C gate, and this pass found a concrete DNS-rebinding attack path the earlier rating missed — fixed by pinning every request to the exact addresses the guard already validated, closing the gap rather than re-documenting it as a residual. Also fixed: `FitResult.score` had no range check, so a live model response of `NaN` or an out-of-contract value would have persisted silently; and a network fault mid-LLM-call used to leave no ledger row at all, even though tokens may already be billed by that point. A walkthrough finding is carried forward, not fixed: `CHANGELOG.md` has grown past what a skimming reviewer reads in one sitting — noted, not yet acted on. Worked findings and how the gates run: [docs/gate-reviews.md](docs/gate-reviews.md); the full private review notes are not tracked in this repo.

**Cost envelope.** No real dollar figures exist yet — every call through Phase C ran against stub clients or hand-captured through Claude.ai chat, never a live billed `AnthropicClient` request, since this project isn't using the Anthropic Console (see B2b/C2b above). What does exist: every call path is instrumented from Phase A onward (D5), the ledger schema and `jscc costs` reporting are built and tested against synthetic call records (percentile latency, per-feature grouping, stale-rate regression detection), and — as of the Phase C → D gate — a call that fails mid-request now leaves a marked row instead of vanishing from the ledger entirely. The honest claim today is "the cost-transparency machinery is built and correct," not "here is what this costs to run" — that second claim waits on a live key, which may not happen under the current no-Console-account decision.

607 pytest cases. Lint and format enforced via ruff (see Development, above).

## License

MIT — see [LICENSE](LICENSE).
