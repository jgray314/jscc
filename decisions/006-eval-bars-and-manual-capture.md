# ADR 006: eval bars, manual capture, and deterministic grading

**Status:** Accepted (2026-09-20, Phase D gate). Records decisions made across
Phases B to D that had not been written down in one place.

## Context

Every LLM stage ships behind an eval suite. Three questions shaped all four
suites and the numbers the README publishes:

1. Where does the evidence come from, when this project has no API key?
2. What pass rate is "good enough" for each stage, and is one number enough?
3. Who or what grades an answer that is prose rather than a field?

## Decision

**Manual capture, replayed.** Real model output is captured by hand: each case's
exact prompt is pasted into a fresh Claude.ai chat, the reply is pasted back, and
`eval <suite> --manual` records it keyed on a hash of the model, system prompt and
sanitized user prompt. `--replay` then grades those recordings without a model.
Proxy runs (a model answering inside a coding agent) are allowed for iterating on
wording, but never recorded. Routing round 4 is why: proxies passed a case the
real chat round failed.

**A bar per stage, set by what a miss costs.**

| Stage | Bar | Second gate | Why |
|---|---|---|---|
| extraction | 80% | — | A wrong field is visible and cheap to correct. |
| scoring | 80% | — | Bands are wide; a miss is a judgment call near a boundary. |
| routing | 85% | zero false-routine | A routine answer leads to a draft. Drafting a situation that needed a person is the one failure the drafter exists to prevent, so it fails the run whatever the rate. |
| composition | 75% | zero drafts on must-ask cases | The grader is strict per case and fails some good emails (see below). Inventing a fact the candidate never gave is the failure that matters, and it fails the run on its own. |

The bars live in code (`jscc/evals.py`), and each zero-tolerance gate has a test
that fails if the gate is removed.

**Deterministic grading, no judge.** Structural fields are compared exactly or
after normalization; scores against a band. Composition prose is checked by rules:
required content by synonym group, a no-invention trap list, a word range, and no
long verbatim reuse of the style samples. Tone is not graded, so a composition
pass means "no mechanical defect", not "a good email".

**Results are pinned.** `tests/test_published_results.py` replays every recording
in CI and fails if a suite's count changes, so the numbers in `evals/README.md`
cannot drift from the evidence.

## Alternatives considered

- **Live API traffic in CI.** Rejected for now: there is no Console account, so no
  key. It would also turn CI into a flaky, billed, nondeterministic check. Replay
  gives determinism; what it cannot give is a judgment on new input.
- **An LLM judge for prose.** Rejected for this stage of the project. A judge is a
  second model whose own errors need an eval, and at 25–28 cases its variance
  would be a large share of the signal. Deterministic rules are weaker but
  inspectable, and they fail loudly on the defects that were actually seen
  (verbatim reuse, invented numbers and names).
- **One bar for every stage.** Rejected because the stages fail differently. A
  single 80% would be too loose for routing and, with this grader, too tight for
  composition, while saying nothing about the failures that matter most.
- **Proxy capture as evidence.** Rejected after routing round 4, above.

## Consequences

- Every headline number is a small hand-captured sample: 25–36 cases, standard
  error roughly 6–9 points. A single round is reported as a first data point. The
  project rule is two rounds on the same prompt in the same band before a suite is
  called validated. As of 2026-09-24 only fit_scoring meets it (27/28 twice, on a
  prompt tuned after a failed first capture). Extraction's current prompt has one
  round (32/36, after 56% and 75% on earlier wording), so it is not validated by
  this rule; composition and routing have one round each, and routing's was tuned
  on its own cases. "Band" here means the spread of rounds with the prompt held
  fixed, not the path a prompt took while it was being fixed.
- Routing's 26/26 was reached by tuning on the same cases, with no held-out set.
  The next routing capture is where fresh cases get added.
- Any change to a prompt, or to what the sanitizer redacts, changes the recording
  keys. That invalidates the recordings for that stage and needs a new capture
  round, which is why low-value prompt changes get batched.
- Grader changes can move published numbers without any model call; the pinning
  test makes that a visible, deliberate edit.
