# Changelog

Slice-by-slice arc. **Phases A and B are summarized** — both are closed and
hardened, and the per-slice detail is in git history where it belongs. Phase C
entries keep their reasoning, because that work is current and the reasoning
is still load bearing. Review findings are recorded here rather than in code
comments.

This file gets compacted one phase behind the current one as a standing
practice, not a one-off: Phase A was compacted when Phase B was the current
work; Phase B is compacted here, now that Phase C has shipped and closed its
own gate. Expect Phase B's summary below to become the next one folded down
once Phase D closes.

## [Unreleased]

### D4c: composer `needs_input` safety net; composition suite 25 -> 28

Defense in depth behind the router's missing-information rule: the composer must
not fill a gap in the record with an invented fact either. It can now decline.

- `DraftEmail` gained `needs_input: str | None` (`subject`/`body` default to
  empty). The composition prompt offers `{"needs_input": "<missing detail>"}` in
  place of a draft, for a reply that cannot honestly be written without a fact
  the input lacks, and says not to escalate when the next action already records
  the decision or a general reply would serve. If a response carries both a draft
  and `needs_input`, `needs_input` wins (a draft beside "I don't know X" is built
  on a guess).
- `followup()` turns a `needs_input` draft into a `Briefing` naming the missing
  detail (`briefing_for_missing_input`), and now forwards `conn` to the composer,
  so production composition calls land in the `composition` ledger feature (before,
  only routing did). The CLI reports a `CompositionParseError` as `drafting
  failed` instead of a traceback.
- Suite 25 -> 28: three `expect_needs_input` fixtures. The grader passes them only
  on an escalation that names the detail, and fails a drafting case that escalates
  (`unexpected_needs_input`).
- Proxy calibration (advisory, Sonnet, not recorded): first wording escalated
  correctly 3/3 but over-escalated 3 of 25 drafting cases (unrecorded slot, Zoom
  answer, availability); after adding the "do not escalate just because" clause,
  those drafted 9/9 and the 3 escalation cases escalated 9/9.
- The composition prompt changed, so D4b's manual capture has not been run against
  this prompt yet.

### Router missing-information rule; routing suite 20 -> 26; composition fixture fixes

Composition proxy runs had the drafter invent a personal fact (a dietary answer)
in every round: it never says "I don't know", so the router has to keep a reply
that needs an unrecorded detail away from it. Decided with Jess 2026-09-19: a
**lenient** rule. Accepting or confirming a single proposal the candidate's own
next action names stays routine; a reply that must state a detail the history
and next action do not supply (dietary needs, availability, which of several
proposed slots) is non_routine, with the missing detail named in `reason`.

- `ROUTING_SYSTEM_PROMPT` gained the rule. It changes the hashed prompt, so the
  20 recordings in `evals/routing/recorded.json` no longer replay; a new manual
  round (D2b round 4) is required before the routing gate holds again.
- Routing suite 20 -> 26: 4 non_routine cases (dietary needs unknown, slot pick
  unrecorded, availability unrecorded, candidate withdrawing) and 2 routine
  boundary anchors where the next action records the answer. SE at 85% is about
  7pt. `routine-logistics-confirm` was first kept unchanged as a boundary probe; it flipped to non_routine on a proxy sample (its history asks about a video option and never answers it), so the unanswered question was removed and `routine-video-option-recorded` covers that shape.
- Composition fixtures: `onsite-travel-logistics`, `interview-availability-confirm`
  and `logistics-confirmation` now carry the answer in their notes, so they are
  cases the router would route routine; style samples that contradicted the new
  facts were replaced. Count stays 25.
- Haiku proxy iteration (advisory): the first wording let the slot-pick case
  through as routine, and one lucky pass hid that; two more wordings later it
  held 3/3 with both routine anchors 3/3.

### D4b calibration -- stock phrases and the form-letter advisory

Proxy runs on Sonnet (25 cases, three rounds, advisory only) showed the 6-word
verbatim-reuse check flagging good drafts: the fixtures' style samples are
one-line answers to their own situation, so echoing a polite convention read as
a defect. Decided with Jess 2026-09-19:

- 21 stock phrases (her seven plus additions drawn from the proxy drafts) count
  as one unit in the reuse check; the 6-unit limit is unchanged, so a whole
  copied sentence still fails.
- `EvalCaseResult.advisories` and a `form_letter` advisory for drafts with more
  than 4 distinct stock phrases. Advisory only.
- Replayed over the 75 proxy drafts: 23/25, 22/25, 22/25 (was 23, 20, 20). The
  remaining failures are one borderline exact-6-unit reuse, a whole copied
  sentence, a fact claim, and the dietary trap.
- +8 tests (525 total).

### D4b (grader) -- deterministic composition grading

`grade_composition` is no longer presence-only. Decided with Jess
2026-09-19: deterministic checks in code, tone not graded (a Jess-graded tone
pass stays a possible follow-up).

- Generic checks: placeholders/redaction tokens, body 30-160 words, subject
  at most 10 words (prompt now asks for 8 or fewer), invented numbers, copied
  style-sample sentences, invented capitalized names.
- `CompositionEvalCase` gained `must_include` (any-of groups) and
  `must_not_include`; all 25 fixtures carry them. Style samples are excluded
  from the facts corpus on purpose: two fixtures' samples contain facts the
  candidate never stated (a time, a dietary answer) and are traps.
- +24 tests (517 total) in `tests/test_composition_grading.py` and
  `test_evals.py`.

### D4a -- composition prompt + call path

Mirrors D2a: the real prompt and the full D7/D8 call path, ahead of D4b's
manual-capture validation against real model output.

- `compose_followup(app, history, intent, style_samples)` is now a real
  `@instrumented` Sonnet call (`COMPOSITION_MODEL` aliases `SCORING_MODEL`,
  one rate entry to keep in sync) through sanitize -> verify -> client, with
  a `composition` / `composition_eval` ledger split, a truncation check, and
  fence-tolerant parsing into `DraftEmail`.
- `COMPOSITION_SYSTEM_PROMPT`: facts only from the input (no invented names,
  topics, times), one purpose per intent, voice matched to the style samples
  without copying them, 50-130 words, no name signature or bracketed
  placeholders, redaction tokens never echoed.
- `StubCompositionClient` returns an empty subject on purpose so an
  unconfigured run fails the presence grader instead of reading as 25 passes.
- `eval composition` gained `--data-dir`/`--record`/`--replay`/`--manual`/
  `--min-pass-rate`; bar is `COMPOSITION_PASS_THRESHOLD = 0.75`.
- Removed `CompositionNotImplementedError` (the D3 stub's marker; nothing
  raises it any more).
- +22 tests, and two D5 tests removed with the error path they exercised
  (493 total). Grading is still presence-only: the quality rubric is the open
  D4b decision.
### D5: briefing renderer + `followup` orchestrator

`jscc followup <application-id>` routes, then either drafts (routine) or
prints a briefing card (non-routine). Built ahead of D4, so its routine path
initially raised `composition unavailable`; D4a has since landed the prompt and
removed that error path.

- `render_briefing` is deterministic: the card is the router's own
  `reason`/`considerations` plus application fields. No second LLM call.
- A routine decision with no `intent` degrades to a briefing instead of
  composing against a guess, in line with D10's bias toward a human.

### D3 resize -- composition suite 8 -> 25 cases, timestamps pinned

Resized before D4b spends any capture effort. SE at D4's 75% bar was ~15pt
at n=8; n=25 gives ~8.7pt. 17 routine cases added (sparse-history and
specific-topic cases for the no-hallucination and prior-touchpoint rubric
axes, repeat nudges, multi-panel and skip-level thank-yous, concrete
logistics). Every fixture now pins `created_at`/`updated_at` so a prompt
built from it hashes deterministically, the same latent bug routing hit in
D2b. Tests: case-count test resized, new test that every case pins both
timestamps (458 total). The `graceful-decline` fixture (candidate withdraws
from a process) was replaced with a routine `prep-guide-acknowledgment` case:
the router prompt treats reply-that-commits-to-an-outcome as non_routine, so
that situation never reaches composition. Count stays 25.

### Parser fence tolerance (routing, extraction, scoring)

D2b's chat captures showed a completion can arrive wrapped in a ```json
fence even though the prompts forbid it. The three `_parse_response`
functions were plain `json.loads`, so a fenced but otherwise correct reply
would have raised a parse error.

- `json_utils.strip_code_fence` unwraps one fence that encloses the whole
  response; all three parsers call it before `json.loads`.
- Deliberately narrow: JSON is never fished out of surrounding prose, and an
  unterminated fence is left alone, so a model that stops following the
  format still fails loudly.
- Recording keys hash the prompt, not the completion, so `recorded.json`
  files are unaffected.
- +23 tests (457 total) in `tests/test_fence_tolerance.py`.

### D3 -- composition eval suite

Per D10 step 2A, composition is reached only for a `routine` classification
-- this suite makes the composer's output gradeable ahead of any real
prompt, the same shape B1/C1/D1 took ahead of their own B2/C2/D2 prompts.

- `DraftEmail` model: the contract Slice D4's prompt is written against.
  `subject`/`body` only -- meant to be pasteable straight into an email
  client, not a structured object needing further assembly.
- `composition.py`: `compose_followup(app, history, intent, style_samples)
  -> DraftEmail` stub, raises `CompositionNotImplementedError` until D4.
  Signature is final now, matching D4's "Sonnet prompt takes application +
  history + intent + 1-3 in-prompt style samples" plan.
- `evals/composition/cases.json`: 8 hand-authored (application, history,
  intent, style_samples) fixtures, all routine -- post-interview thank-you,
  cadence nudge, onsite-logistics confirmation, a cold recruiter-outreach
  acknowledgment, thank-you after a phone screen, thank-you to a referrer, a
  graceful decline (later replaced, see the resize entry), and confirming availability for a proposed interview
  time. No non-routine case exists here by design: D10's routing step
  (D1/D2) already refuses to route a non-routine situation to composition at
  all.
- `evals.py`: presence-only grading (non-empty `subject`/`body`) -- the same
  deferral `responsibilities_summary` (B1), `rationale` (C1), and
  `intent`/`reason`/`considerations` (D1) each got: real quality grading
  (the LLM-judge rubric the parent plan names -- tone match, reference to
  the prior touchpoint, no hallucinated facts, appropriate to the declared
  intent) waits on Slice D4's real prompt.
- `python -m jscc eval composition` CLI command, no `--record`/`--replay`
  yet -- that machinery lands with D4, same as the other three suites.
- +7 tests (434 total). DoD met: runs, reports 0/8 passed (no prompt yet).

### D2a -- routing prompt + call path

Mirrors B2a's and C2a's shape: the real prompt, the full D7/D8 choke-point
call path, and CLI wiring, ahead of D2b's manual-capture validation against
real model output.

- `llm_client.py`: `ROUTING_MODEL` (aliased to `EXTRACTION_MODEL` -- per
  D10 routing is "Haiku, cheap," the same shape extraction already is, so
  it reuses that model id and rate entry rather than hand-copying a second
  one that could drift out of sync). `StubRoutingClient` +
  `default_routing_client()`, same shape as the other two stubs with one
  deliberate difference: its fixed answer is `non_routine`, not an
  arbitrary placeholder -- per D10's bias, an unconfigured router that
  never auto-drafts is the honestly correct "safe when uncertain" default,
  not just a stand-in for one.
- `routing.py`: real `ROUTING_SYSTEM_PROMPT` biased hard toward
  `non_routine` on any genuine uncertainty, per D10's explicit instruction
  that a false-routine auto-draft is a much larger failure than a
  false-non-routine briefing card. `route_followup` wired through the same
  sanitize → verify → instrumented-call path as `extract_jd`/`score_fit`,
  given the application and its interaction history as one JSON payload.
  `route <application-id>` CLI command reads the application + its
  interactions and prints the decision -- no `Application` field persists
  it yet, since nothing downstream (composition, the briefing renderer)
  exists to consume it.
- `eval routing` gained `--record`/`--replay`/`--manual`/`--min-pass-rate`
  parity with `fit_scoring`, plus a second gate specific to routing: per
  D10, `false_routine_cases` scans results for any genuinely `non_routine`
  fixture classified `routine` and fails the run on that alone, regardless
  of the combined pass rate (`ROUTING_PASS_THRESHOLD = 0.85`, stricter
  than the other two suites' 80%). A prompt could clear 85% overall while
  still auto-drafting something it shouldn't; this refuses to call that
  passing.
- **Grading nuance found while wiring the stub test, same shape C2a hit:**
  `StubRoutingClient`'s fixed `non_routine` answer isn't a no-op against
  this suite the way a fixed placeholder was for extraction -- it
  trivially clears the false-routine gate (it never says "routine," so
  there's nothing to be a false-routine case) while landing at 50% on the
  combined bar (6/12 -- every non_routine-expected case passes, every
  routine-expected case doesn't, since the stub can't tell them apart).
  "Every case fails" isn't the invariant here any more than it was for
  `fit_scoring`'s stub at C2a.
- +14 tests (427 total, `tests/test_routing.py` new). Verified end-to-end
  against the stub: `route <application-id>` reads an application + its
  interactions and prints a decision; `eval routing` reports 6/12 (50%,
  below the 85% bar) with zero false-routine cases; `jscc costs` shows the
  call under its own `routing` ledger feature.

### D1 -- routing eval suite

Per D10, the drafter's first step is a Haiku classification call (routine /
non_routine), not a draft -- this suite makes that judgment gradeable ahead
of any real prompt, the same shape B1 took ahead of B2 and C1 took ahead of
C2.

- `RoutingDecision` model (plus `RoutingClassification`): the contract
  Slice D2's prompt is written against. Two shapes on one model rather than
  a tagged union, matching how `ExtractedJD`/`FitResult` already parse
  straight out of a model's JSON response -- a `routine` decision carries
  `intent` and leaves `reason`/`considerations` unset; a `non_routine`
  decision carries `reason` + `considerations` and leaves `intent` unset.
- `routing.py`: `route_followup(app, history) -> RoutingDecision` stub,
  raises `RoutingNotImplementedError` until D2. Signature is final now.
- `evals/routing/cases.json`: 12 hand-authored (application, history)
  fixtures split evenly across the routine/non-routine surface D10 names --
  routine (post-interview thank-you, cadence nudge on a stale screen,
  onsite-logistics confirmation, a cold recruiter outreach, thank-you after
  a phone screen, thank-you to a referrer) and non-routine (a
  feedback-seeking rejection reply, a compensation negotiation, first
  outreach to a warm personal contact, two threads giving conflicting
  instructions, an interaction note carrying a contact's personal/medical
  disclosure per D8, and a genuinely ambiguous recruiter check-in with no
  clear ask).
- `evals.py`: classification-exact grading as the case-defining check, plus
  a shape-appropriate presence check (`intent` for routine, `reason` +
  `considerations` for non_routine) -- the same presence-only treatment
  `rationale` got at C1, deferring real wording-quality grading (is
  `intent` the *right* bucket, are `considerations` actually useful) to D2
  once there's a prompt worth judging.
- `python -m jscc eval routing` CLI command, no `--record`/`--replay` yet --
  that machinery lands with D2, same as extraction's did at B2a and
  scoring's did at C2a.
- +9 tests (413 total). DoD met: runs, reports 0/12 passed (no prompt yet).

### Phase C -> D gate: G1 (SSRF pin), G2 (score bounds), G3 (failure-marker ledger row)

A cold two-lens review (adversarial + outside-reviewer walkthrough) ran
against everything Phase C shipped -- the first review pass to touch
`scoring.py`, `cost_report.py`, or `evals/fit_scoring/`. `/backlog-prune`
ran first, per the gate skill's own step 0 (see `jscc.md`'s backlog and
"Open decisions" sections for that disposition).

- **G1 (CONTRADICTS-L-2) -- fixed.** The fetcher's URL guard checked one DNS
  resolution (`_check_url`) but let `requests` perform its own, independent
  resolution to actually connect -- a short-TTL DNS record could answer the
  check with a public address and the real connection, moments later, with
  a private or cloud-metadata one. An earlier gate pass (L-2) rated this
  "reasoned not exploited" and closed it by documenting the residual in D6;
  this pass re-examined it with a concrete attack path and found the "not
  exploited" call didn't hold. `_check_url` now returns the validated
  `(host, addresses)`, and `_get_guarded` wraps every request -- including
  each redirect hop -- in a new `_pinned_resolution` context manager that
  forces any DNS lookup for that host, for the duration of the request, to
  return exactly what was already checked. Verified with a test that
  simulates a "live" resolver answering a different (rebinding) address and
  confirms the pinned block still connects to the validated one, with the
  patch restored (not left globally active) once the block exits.
  `docs/design-principles.md`'s D6 section rewritten to say "fixed, not just
  documented" for this gap specifically, naming the earlier rating as wrong
  rather than quietly dropping it.
- **G2 (NEW) -- fixed.** `FitResult.score` had no range or finite-value
  check, even though the scoring prompt contracts it to 0-100. `json.loads`
  accepts `NaN`/`Infinity` by default, so a live model response containing
  either -- or any out-of-contract value like `-40` or `9001` -- parsed
  cleanly and would have persisted silently. `score: float = Field(ge=0,
  le=100)` closes it; verified directly (not assumed) that the range check
  also rejects `NaN`/`Infinity`, since every comparison against either is
  `False`.
- **G3 (NEW, extends M2's class) -- fixed.** M2 (Phase B) covers a billed
  call that returns cleanly and then fails to *parse*. This is the sibling
  gap: a network fault (connection reset, read-timeout) raised *during* the
  call, possibly after tokens were already generated/billed on the
  provider's side, left no ledger row at all -- not even a failure marker --
  because `instrumented`'s wrapper only wrote a row after the wrapped call
  returned normally. `llm_calls` gained a nullable `error` column
  (`DB_SCHEMA_VERSION` 3 -> 4; this project has no live migration path
  pre-v1, per the existing schema-version convention). `instrumented` now
  catches an exception from the wrapped call, writes a marker row (zeroed
  usage, `error` set to the exception's type and message, real latency),
  and re-raises unchanged -- callers' existing DLQ-routing behavior for
  these exceptions is untouched. `summarize_costs`/`find_cost_regressions`
  exclude `error`-set rows from cost/latency math, the same treatment an
  unpriced model already gets, since a zeroed row isn't a real measurement;
  `list_llm_calls` still returns them for a reader who wants failed-attempt
  visibility.
- **G4 (NEW) -- re-deferred, not fixed.** `scoring.py` sends the full
  `Profile` (including `display_name` and free-text `style_samples`) to the
  scoring LLM though none of the five weighted factors use either field.
  Discovered mid-fix: any change to the scoring payload changes the exact
  "user" prompt text, which invalidates all 25 of C2b's just-closed
  manual-capture recordings (keyed by a hash of model+system+user, per
  H-5). Not worth burning a just-closed validation round for a Low-severity
  minimization finding -- re-deferred to next time the scoring prompt
  changes for another reason anyway.
- **G5 (REPEAT-OF-L-3)** -- reconfirmed still open, no severity change; the
  `_CONTROL_KEYS`/`model`-key redaction exemption in `sanitizer.py` still
  applies at any nesting depth. No live payload nests under `model` today.
- **W-15 confirmed live, fixed.** The walkthrough lens flagged a *pattern*
  (README status prose going stale within hours of a shipping commit) with
  no current instance found -- checking anyway turned up a real one:
  README's opening paragraphs and "Built and shipped" section still
  described "Phase B shipped, before Phase C" days after Phase C actually
  closed, directly contradicting the Status table two screens below in the
  same file. All four spots rewritten; a new "Phase C -> D gate" paragraph
  added alongside the existing "Phase B -> C gate" one. Cost envelope
  paragraph (deferred since Phase A) written for real rather than
  re-deferred again -- see below.
- **W-12 (NEW), W-13 (NEW), W-14 (NEW)** -- not fixed this pass. CHANGELOG's
  length (now past 1,500 lines), `cli.py`'s size, and fit_scoring's
  single-round validation are all logged in `jscc.md`'s cleanup backlog with
  their own dispositions (a docs-restructuring decision for Jess, a
  Phase-D-triggered refactor, and a second manual-capture round only Jess
  can run, respectively).
- 404 tests (+12 total this pass), lint/format/scanner clean. Full findings
  and disposition: `jscc-phase-b-rerun-gate.md` (not tracked in this repo).

### C3 - cost/latency reporting, Phase C closed

Instrumentation (D5) has landed one `llm_calls` row per call since Phase A;
this slice is the reporting layer that finally reads it back. New
`cost_report.py`, pure functions in `report.py`'s style -- no DB access, the
CLI is a thin wrapper.

- `summarize_costs`: groups by feature, reports calls/total-cost/avg-cost
  plus `p50`/`p95` latency (nearest-rank `percentile`) instead of just an
  average, so a slow tail doesn't hide behind a good mean.
- `find_cost_regressions`: turns "per-slice cost regression tracking" into
  something concrete without inventing a new schema field. For every call
  whose model has a rate on file, recompute the expected cost from its
  token counts and flag a drift beyond 1% relative / $0.0005 absolute (float
  noise floor) -- the exact discrepancy shape B12 caught by hand, where a
  stale rate silently under-recorded every call by a fixed factor. A call
  against an unknown model (stub clients, test fixtures) is skipped, not
  flagged -- it was never priced against a real rate to drift from.
- `costs` CLI command (the minimal per-feature/avg-latency version that
  already existed ahead of this slice) rewired onto the new module; same
  command, richer report.
- +12 tests (392 total): percentile edge cases, per-feature aggregation, a
  correctly-priced call passing, a B12-shaped mispriced call flagged, float
  rounding tolerated, and two CLI integration tests.

**Phase C closed.** C1 (eval suite), C2a (prompt + plumbing), C2b (84% on
round 1 manual capture, one deferred finding), and C3 (this slice) are all
shipped. Phase D (routing/drafter) is next.

### C2b round 1 - 84% on the first capture, above the 80% bar

Ran `eval fit_scoring --manual` for real: all 25 cases hand-captured through
Sonnet 4.5 chat (Jess's own session; a coding agent generating the
completions itself would misrepresent the data's provenance in a project
whose signature signal is honest eval-driven validation). 21/25 passed.

Four misses:

- case-02, case-06, case-08 -- comp/level boundary judgment landing just
  outside a band, consistent with the pre-round notes' prediction that
  these are inherently unstable judgment calls, not wording gaps.
- case-14 (director/VP posting, one level above the profile's L6-L7
  target, comp above range) -- scored 38 against a 70-95 expected band.
  Spot-checked with a second independent capture: 22, same reasoning both
  times ("far outside role_focus"). Two consistent fails rules out
  capture-to-capture noise -- this is a real ambiguity in the prompt's
  role/level factor, not sampling variance. `role_focus` names only exact
  target titles, so an adjacent higher title reads as categorically
  outside it rather than as one step up that comp should be allowed to
  compensate for. Deferred, not fixed here -- 84% clears the bar and
  doesn't block C3; tracked as a between-phases refinement in
  [jscc.md](../context-directory/projects/ai-portfolio/jscc.md)'s cleanup
  backlog.

Both deal-breaker cases built to be detectable only from raw text or
boilerplate (case-11, case-22) and both IC-profile cases (case-19,
case-20) passed clean.

### C2b prep — `--manual` capture tooling

`jd_extraction`'s manual-capture round (B2b) had no tooling to speak of --
`recorded.json` was hand-edited, one entry at a time, by whoever ran the
prompt through Claude.ai chat. Before starting fit_scoring's own round,
built the thing B2b was missing rather than repeating the same by-hand
process for a second suite.

- `evals.py`: `ManualCaptureClient`, an `LLMClient` whose "network call" is
  a human pasting a prompt into Claude.ai chat and pasting the completion
  back. Prints the exact model id, system prompt, and user message; reads
  the response back line-by-line until a line that is exactly `END` (a
  real completion can contain blank lines, so a blank line can't be the
  sentinel). Reports zero tokens/cost, honestly -- no billed call happened.
- `cli.py`: `eval fit_scoring --manual`. Implemented as `RecordingClient`
  wrapping `ManualCaptureClient` instead of the real API client, so M-12's
  persist-immediately behavior (a capture already paid for -- here, already
  typed -- surviving a mid-run failure) comes for free rather than needing
  its own version. `--manual` implies `--record`; both remain mutually
  exclusive with `--replay`.
- +4 tests (380 total): `ManualCaptureClient`'s prompt display, response
  parsing, and blank-line-vs-`END` sentinel behavior; a CLI test driving
  `--manual` through `CliRunner`'s stdin across all 25 cases and confirming
  the recording file lands with one entry per case.

### fit_scoring case sizing — resized 10 -> 25 before C2b spends any capture effort

Caught proactively rather than discovered mid-round: at the >=80% threshold,
the binomial standard error on a pass rate is `sqrt(p(1-p)/n)`. At n=10
that's ~13 points -- worse than the exact problem `jd_extraction` hit at
n=15 (~10 points), which is what forced its own resize to 33 cases (~7
points) partway through B2b. C2b's validation is manual capture through
Claude.ai chat -- expensive per round -- so spending it against a suite
whose >=80% reading could swing ~25 points on model variance alone would
have repeated jd_extraction's mistake with full knowledge it was coming.

- Added 15 cases (case-11 through case-25): comp partially-below/missing
  bands, level above target with executive scope, role-focus matching only
  one of two profile entries or neither, a deal-breaker detectable only from
  raw JD text (not the structured extraction) including one buried in
  unrelated boilerplate, a must-have satisfied only via raw-text nuance, an
  empty skills list that shouldn't tank an otherwise-strong match, an
  ambiguous "Tech Lead" title, and two cases against a second IC-focused
  profile to prove grading isn't hard-coded to one profile shape.
- n=25 brings the standard error to ~8 points, matching the precision
  `jd_extraction` settled on at n=33.
- The stub's fixed score of 0 now clears 8/25 bands by coincidence (32%),
  still far under the 80% bar -- same qualitative result as before the
  resize, just measured against a suite whose eventual pass/fail reading
  will mean something.
- No new tests (the resize changes fixture size, not test count); updated
  hardcoded `10`s to `25`s in `test_evals.py` and `test_cli.py`.

### C2a — fit scoring prompt + client plumbing

Mirrors B2a's shape: the real prompt and the full call path land now, but
with no `ANTHROPIC_API_KEY` configured for this project, `default_scoring_
client()` resolves to `StubScoringClient` and the eval suite runs end-to-end
at $0 rather than against real judgment. C2b (manual capture through
Claude.ai chat, same as B2b) is what actually validates the prompt.

- `llm_client.py`: `SCORING_MODEL` (Sonnet, per D9's cost/quality split from
  extraction's Haiku), its published rate, `StubScoringClient`, and
  `default_scoring_client()`.
- `scoring.py`: real `SCORING_SYSTEM_PROMPT` weighing deal-breakers first
  (score capped below 20 if one is clearly met), then role/level fit, comp
  band against the profile's target range, must-haves, and skill overlap
  last -- in that order, matching how a real fit judgment should weigh
  disqualifiers over nice-to-haves. `score_fit` now builds the same
  sanitize -> verify -> instrumented-call path `extract_jd` uses, feeding
  the model the extracted JD, the raw JD text, and the full profile as one
  JSON user payload.
- `cli.py`: new `score <application-id>` command -- reads `extracted_jd`
  and `source_raw` off an existing `Application`, loads the active
  profile, scores it, and persists `fit_score`/`fit_rationale` via
  `update_application`. `eval fit_scoring` gained `--record`/`--replay`/
  `--min-pass-rate`/`--data-dir` parity with `eval jd_extraction`, backed
  by its own `evals/fit_scoring/recorded.json`.
- The stub's fixed score of 0 happens to fall inside a few of C1's
  deliberately-low-fit bands, so "every case fails" isn't the right
  invariant here the way it was for extraction's stub (whose placeholder
  values structurally can't match anything) -- what's testable instead is
  that the pass rate stays far below `PASS_THRESHOLD`, since the 10 cases'
  bands collectively span 0-100 and no constant score clears the bar.
- +17 tests (376 total). Verified end-to-end: `ingest --paste` then
  `score <id>` persists a score/rationale and shows up under its own
  `scoring` ledger feature in `jscc costs`, separate from `extraction`.

### C1 — fit scoring eval suite

Per D9, extraction and scoring are split so scoring judgment can be graded
independently of extraction facts. This suite is that independence made
concrete, ahead of any real prompt -- the same shape B1 took ahead of B2.

- `FitResult` model: the contract Slice C2's prompt is written against.
- `scoring.py`: `score_fit(extracted, raw_jd_text, profile) -> FitResult`
  stub, raises `FitScoringNotImplementedError` until C2. Signature is final
  now, matching D9's locked decision that the scorer sees both the
  extracted structured JD and the raw text.
- `evals/fit_scoring/cases.json`: 10 hand-authored (JD, profile) pairs
  across the fit spectrum -- clear high fit, comp below target, level
  mismatch, a deal-breaker present, must-haves entirely missing, a
  borderline hybrid case, comp above target, an ambiguous minimal posting, a
  total role mismatch, and a single must-have miss on an otherwise strong
  match.
- `evals.py`: band-based grading (`min_score`/`max_score`, not an exact
  figure -- a fit judgment doesn't have one right answer) plus a
  non-empty-rationale check, deferring real rationale-quality grading to an
  LLM-judge rubric once there's a prompt worth judging, the same deferral
  `responsibilities_summary` got at B1.
- `python -m jscc eval fit_scoring` CLI command, no `--record`/`--replay`
  yet -- that machinery lands with C2, same as extraction's did at B2a.
- +7 tests (359 total). DoD met: runs, reports 0/10 passed (no prompt yet).

## Phase B — ingestion + extraction (A5, B1–B14, closed 2026-09-04; Phase B → C gate closed 2026-09-12)

Fourteen slices (plus A5, LLM instrumentation deferred from Phase A) building
the first two AI-native stages: the extraction eval suite, the extraction
prompt, the URL fetcher with DLQ, the paste-only escape hatch, and three full
rounds of adversarial review plus outside-reviewer walkthrough — the second of
which ran cold (each reviewer barred from the prior gate doc until it had
formed its own findings) and immediately produced a CONTRADICTS against a fix
from the slice under review. 348 tests at close, plus a lint/format gate added
just before Phase C started. Per-slice detail is in git history; what follows
is the shape and the decisions worth keeping.

**The build**

| | |
|---|---|
| A5 | LLM call instrumentation, deferred from Phase A. Slipped past all three Phase A gate rounds because every round reviewed shipped code, not missing slices — no round had reason to look for what wasn't there. `@instrumented(feature)` decorator, `llm_calls` table (schema v3), `jscc costs`. |
| B1 | JD extraction eval suite. `ExtractedJD` contract, 15 hand-authored JDs, hand-rolled harness (structural fields exact, skills by set equality, prose non-empty only). DoD met: runs, reports 0/15 — no prompt yet. |
| B2 | Extraction prompt v1 + client plumbing, landed behind `StubExtractionClient` since no `ANTHROPIC_API_KEY` exists for this project — scoped honestly as wired, not validated. `EXTRACTION_MODEL`'s digit run trips the phone-pattern scanner; this false-positive class recurred five times across the project (danger-list example, model id, a comment quoting the model id, `uv.lock` hashes, IP literals in tests) before the convention — split the literal or describe the shape, never loosen the regex — stuck. |
| B3a | Baseline fetcher + DLQ core, per D6. `requests` + `readability-lxml`, every non-2xx/exception classified into a `FailureMode`, never raises. `ingest --url`, `dlq list`, `resolve-dlq --paste-text`. |
| B3b | Playwright fallback (opt-in, ~200MB dep, confirmed explicitly) + a real-URL smoke test against 5 live postings chosen via browser, not guessed — an SPA site and an authwalled search both recovered with the flag on; a straight bot-block stayed blocked either way, correctly. |
| B4 | JD paste-only path — the D6 escape hatch for a site the fetcher can't crack at all. Routes through the same helper as the URL path, so "same Application shape" is structural, not conventional. |
| B5–B9 | **Phase B → C gate hardening**, including a cold rerun that produced the project's first CONTRADICTS. Full detail below. |
| B10 | The prose pass — five README/docstring claims that described a system slightly better than the one that existed (see below). |
| B11 | The tracked synthetic fixture stopped being tracked (see below). |
| B12 | Corrected a 20%-under Haiku pricing error before any real spend. |
| B13 | The scanner and sanitizer both learned to catch an Anthropic API key shape, as its own category, not filed under "personal data." |
| B14 | The README's stated test count, fixed and then made structurally unable to drift again — the fourth time that figure had gone stale. |
| Backlog + lint gate | Closed the entire A2/A9/A10-era backlog (walkthrough #5–#7, three low-severity items, one field-whitelist gap) and added ruff (lint + format) ahead of Phase C, which itself surfaced 4 real bugs on first run. Full detail below. |

**What the gates actually changed** — the durable ones, several of which recur
as lessons the Phase C → D gate cited directly:

- **The sanitizer authenticated payloads it never redacted (C1, B5).** `_transform`
  had been an identity snapshot since Phase A, deferring real redaction to "when
  the first prompt is written." Phase B shipped a prompt and two production call
  sites without it — demonstrated concretely by one string that was *blocked from
  git* by the pre-commit scanner and *forwarded verbatim to the LLM* by the
  sanitizer, two mitigations named in the same design principle disagreeing
  completely about what counts as personal data. Fixed as one definition
  (`jscc/personal_data.py`), two enforcement points; redaction is unconditional
  and runs before the HMAC authenticator, so no caller can opt out and no
  verified payload can carry the original text. Scope stated honestly: structured
  identifiers and danger-list literals, not free-text NER.
- **The same drift recurred through path resolution, not duplicated regexes
  (H-1/M-3, B6).** The danger list and the data directory both resolved against
  the process's working directory rather than the package root, so running from
  anywhere but the repo root silently emptied the name list and put `real.db`
  outside its own `.gitignore` — one wrong `cd` disabled two mitigations at once,
  invisibly, since email/phone redaction kept working. Fixed with one
  `PACKAGE_ROOT` every module anchors to, per the same "one definition, two
  enforcement points" argument C1 had just made about the rules themselves — the
  lesson generalizes past the specific regex it was first learned on.
- **A test that cannot fail when the thing it covers is deleted is not covering
  it (H-4, M-4, B8).** The M5 fetch guards and the sanitizer redaction path both
  survived their own removal without a single test failing, because every guard
  test patched `requests.get`/`extract_jd` wholesale rather than observing the
  guard's actual behavior. Both fixed by verifying at a lower level (a spy on
  `HTTPAdapter.send`; a spy client checking the redacted text actually crosses)
  and confirmed by deletion, not by passing.
- **"Nothing that can fail after the money is spent belongs inside an
  instrumented function" (M2, B5/B9).** A billed call that failed to parse, or
  decoded incorrectly, or got its usage discarded (H-3) all shared one root
  cause: work capable of failing was running *inside* the ledger-writing wrapper,
  so the exception beat the write. The rule, not just the individual fixes, is
  what carried forward — B9's response-decoding fix and the eventual Phase C → D
  gate's G3 (a network fault losing the ledger row entirely) are both instances
  of the same principle applied to a new point of failure.
- **A transient failure has to produce a DLQ entry, not a crash (H-2, H-6,
  B6/B9).** Both an extraction parse failure and a transient Anthropic API error
  used to propagate straight out of the SDK call, crashing `ingest`/`resolve-dlq`
  with no DLQ row to retry from — violating D6's own contract ("produces an
  Application or a DLQEntry, never crashes") for exactly the failure modes a live
  key would actually hit.
- **A cold second-opinion review beats an anchored one (B6).** Rerunning both
  lenses with neither reviewer shown the prior gate's findings produced a
  CONTRADICTS against a fix from the very slice under review, and both lenses
  independently found the same top hole (the danger-list path bug above) —
  neither would likely have surfaced under the anchored method the first pass
  used. This is the method the Phase C → D gate also ran cold, and it produced
  its own CONTRADICTS there too.
- **The repo can describe a system slightly better than the one that exists,
  and a cold walkthrough reader notices immediately (B10, B14, lint-gate
  README fix).** The published sample output didn't reproduce under the words
  "Bit-reproducible"; the README's stated test count went stale four separate
  times; a self-contradicting Status paragraph got fixed at one location and
  stayed wrong at another. The durable fix each time was structural, not a
  promise to remember: `report --now` sharing one parser with `seed` so the
  sample can't drift, a test comparing the README's stated count against what
  the suite actually collected, and — eventually — the compaction this section
  is itself part of.

**Still open from Phase B: nothing.** Every finding across the three gate
passes (2026-09-01, the cold rerun at 2026-09-04, and a third pass the same
morning B2b closed) is closed, fixed, or documented as an accepted residual as
of 2026-09-12 — full audit trail in `jscc-phase-b-rerun-gate.md`. The entire
carried-forward A2/A9/A10-era backlog (walkthrough #5 ADR-001 framing fixed,
#6 coverage badge killed, #7 CHANGELOG split killed the first time — see the
note at the top of this file for why it came back — plus the `update_application`
field-whitelist gap and three low-severity sanitizer/report findings) closed
the same day, caught by the first `/backlog-prune` sweep rather than riding
through indefinitely. Ruff (lint + format) landed the same week, ahead of
Phase C, and surfaced 4 real bugs — a stale `__all__` export, three swallowed
exception chains, an unverified blind `pytest.raises(Exception)`, and a Python
version the codebase could already assume but hadn't used — on its first run.

---

## Phase A — foundations (A1–A10, closed 2026-08-29)

Ten slices building the non-LLM substrate: config, storage, a deterministic
synthetic fixture, staleness reporting, the dual-use safety architecture, and
three full rounds of adversarial review plus outside-reviewer walkthrough. 142
tests at close. Per-slice detail is in git history; what follows is the shape and
the decisions worth keeping.

**The build**

| | |
|---|---|
| A1 | Package scaffold, pydantic config models for `stages.yaml` / `profile.yaml`, `validate-config`. **ADR-001** (pydantic over jsonschema). |
| A2 | Storage: pydantic domain models, SQLite schema with FKs and cascade rules, CRUD with a field whitelist and auto-touched `updated_at`, `PRAGMA user_version` versioning. **ADR-002** (stdlib `sqlite3`), which also documents the single-writer limit and the naive-datetime contract. |
| A3 | Deterministic synthetic seed: 25 applications across every stage, 19 contacts, 40 interactions, 3 DLQ entries. Hardening pass made interaction chains chronologically coherent, wired HM contacts, and varied JD content by role type. |
| A4 | Staleness detector + funnel counts as pure functions over `list[Application]`; `jscc report`. Reference timestamp is `last_interaction_at` when set, else `created_at`. |
| A4.5a | **Environment isolation (D7 M1/M2/M7).** `Mode` enum via `JSCC_DATA`, one DB per mode, a marker stamped *inside* the DB so a cross-mode open raises rather than relying on path convention. `profile.private.yaml` preferred over the tracked example. **ADR-003.** |
| A4.5b | **Content controls (D7 M3/M5).** The pre-commit scanner and the sanitizer skeleton. **ADR-004.** |
| A6–A10 | Three gate rounds and their fixes (below). **ADR-005** documents sanitizer authenticity with six rejected alternatives. |

**What the gates actually changed** — these are the durable ones, and several
recur as lessons in Phase B:

- **Sanitizer authenticity (A6).** `sanitize_for_llm` returns a frozen
  `SanitizedPayload` carrying an HMAC over stable-JSON(data) + timestamp, keyed
  by a per-process secret; `verify()` uses a constant-time compare. Forged
  wrappers, mutated data and swapped timestamps all fail. `SanitizerRefusal`
  inherits from `Exception`, not `ValueError`, so a generic `except ValueError`
  upstream cannot silently swallow a refusal.
- **Mode-check ordering (A6).** Full DDL used to run *before* the marker was
  checked, so a wrong-mode open touched the wrong file before refusing. Now:
  meta-only bootstrap, read the marker, refuse before any DDL. A missing or
  tampered marker on a populated DB raises rather than restamping.
- **Seed determinism (A7, regressed, caught again in A9).** Five of six
  `Interaction` constructions fell back to pydantic's `default_factory=uuid4`,
  which uses `os.urandom` and bypasses the seed. A7 claimed this closed; the A9
  adversarial pass found it still open because **the A7 test hashed only the
  `applications` table**, which was clean. The reproducibility test now hashes
  every table. A test that checks one instance of a class of bug is how a
  regression stays invisible.
- **Deep-copy in `_transform` (A10).** `dict(payload)` was a top-level shallow
  copy, so a caller retaining a nested container could mutate it between
  `verify()` and send — the authenticator would still match while the bytes on
  the wire changed. Now snapshots through the same canonical JSON the HMAC uses.
- **One exclude list, not two (A10).** CI excluded `CHANGELOG.md` from the
  scanner and the local pre-commit hook did not, so `pre-commit run --all-files`
  refused commits on a tree CI accepted. Both now invoke
  `scripts/scan_tracked.sh`. This is the same single-sourcing argument that C1
  later applied to the rules themselves, and H-1 to the files that define them.
- **Supply chain (A10).** GitHub Actions pinned by full commit SHA with the tag
  in a trailing comment; `uv.lock` committed and CI running `--frozen`. Phase B
  introduces API-key secrets, so this had to be right before then.
- **Scanner coverage (A9).** The email regex was widened for IDN local parts,
  non-ASCII TLDs and Punycode — deliberately over-broad, per D7's "false
  positives are the design point." `--exclude` got a real glob-to-regex compiler
  after `fnmatch` was found treating `**` as literal, leaving `tests/**` a hole.
- **Safe surface (A9).** `connect`/`init_db` renamed to `_connect`/`_init_db`
  with `__all__` naming the safe surface and a lock test asserting the primitive
  cannot be re-exposed by accident. `busy_timeout` + WAL added.
- **A8** added the README's three-idea framing, `docs/design-principles.md`
  (D1–D10 inlined, each recording what was chosen and the alternative rejected),
  MIT license, and CI.

**Three CI hotfixes, one lesson.** The scanner went red on `uv.lock` hashes, on
a comment that quoted the very strings it was explaining, and on a stale action
pin. The first two were self-inflicted prose, not the regex being oversensitive
— see the convention recorded under B2. The lockfile incident also exposed that
`set -euo pipefail` did not propagate `xargs`'s exit through the pipeline on Git
Bash, so the wrapper reported success on failure; it now checks the exit code
explicitly.

**Left open from Phase A at the time, all closed by 2026-09-12** (see the Phase
B summary above): walkthrough #5 (ADR-001 framing, fixed), #6 (coverage badge,
killed), #7 (CHANGELOG split, killed then revisited — see the note at the top
of this file), and L-json-default-sanitizer-1 (fixed once Phase C's fit-scoring
payload gave it a real trigger). Caught by the first `/backlog-prune` sweep
after sitting untouched across two phase boundaries — nothing here rides
silently through a third one now that the sweep exists.
- L-report-format-injection-1 — control-char escaping in `format_report`.
