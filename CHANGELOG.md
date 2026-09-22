# Changelog

Slice-by-slice arc, newest first. Phase D, the phase that just closed, keeps its
reasoning in full. Phases A to C are summarized: the shape of the build and the
lessons worth keeping, with per-slice detail in git history. Review findings are
recorded here rather than in code comments.

Standing practice: at each phase gate, the phase before the one that just closed
is folded into a summary. Phase C was folded at the Phase D gate; Phase D will be
folded at the Phase E gate.

Slice names (A1, B2b, C2a, D4c...) are build steps. They are unrelated to the
design principles D1 to D10 in `docs/design-principles.md`.

## Phase E — dashboard (in progress)

### E1: web scaffold (ADR-007)

Resolved the deferred web-stack discussion (parent-plan queue #4): FastAPI + Jinja2 +
HTMX, no SPA, no separate JS build step — the dashboard stays in JSCC's existing
Python toolchain, matching the "earn its slot over the fancier default" judgment
already applied to the LLM-stage splits (D9/D10). ADR-007 records the alternatives
(React/Vite, plain server-rendered HTML, Streamlit/Gradio) and why they lost.

`jscc/web/` holds a `create_app(data_dir, config_dir)` factory and Jinja2 templates;
`jscc serve` boots it (127.0.0.1 by default, local-only per D4 — no BYOK). The index
route opens the active mode's DB through the same `open_for_mode` contract the CLI
uses, shows the application count, and renders the SYNTHETIC MODE banner (D7 M6) a
slice early, since it was cheap to add once the template existed. +3 tests (622 total).
Pipeline/funnel/stale views (E2a) and application detail + DLQ resolve (E2b) are next.

### E2a: pipeline, funnel, and stale-alert views

The index route now renders what `jscc report` already prints as text: a funnel
table (every configured stage, including zero-count ones), a pipeline table
grouping applications under their stage, and the stale-alert list. All three read
`report.py`'s pure functions — `funnel_counts`, `detect_stale`, and a new
`group_by_stage` added alongside them — so the CLI and the dashboard render the
same underlying computation and cannot silently disagree on what counts as stale.

A `?now=` query parameter mirrors the CLI's `--now`: same ISO-8601-with-timezone
format, same 400-on-bad-input framing as `report`'s UsageError, so a pinned seed's
stale block is reproducible from a browser the same way it already was from a
shell. Manually verified against the seeded synthetic fixture and `--now
2026-08-28T12:00:00+00:00`: funnel counts, pipeline listing, and all 13 stale
alerts matched `jscc report`'s output line for line. +7 tests (629 total).
Application detail + DLQ resolve (E2b) are next.

## Phase D — follow-up drafter (D1–D5, Phase D gate 2026-09-20)

Routing first, then composition for routine situations only, and a briefing card
for everything else (design principle D10). Both prompts were checked against real
model output captured by hand. The phase closed with a cold two-lens gate whose
fixes are the first entries below.

### Capture tooling, and a checklist for the next round

The scripts that ran the routing and composition capture rounds lived in a session scratchpad and were
rebuilt between D2b and D4b. `scripts/capture_tools.py` now does it for all four suites: it builds each
case's prompt from the current code, prints a case for the chat with the target model at the top and
bottom (the routing round captured on the wrong model once), records a completion under the key replay
looks up, and runs the proxy loop. It refuses stale prompts, and refuses to record any file under a
`proxy` directory, so proxy output cannot reach a recording. +11 tests (619 total).

`evals/README.md` gained a "Before a capture round" checklist: freeze the prompt, run the full suite
through proxies on the final wording, read some outputs for defects the grader does not check, date the
fixtures whose answer depends on today, check the model. The parent plan now says to size a suite before
authoring it: at least 25 cases, with n and its standard error stated in the slice's definition of done.

### Phase D gate: prose and review hygiene

The walkthrough lens's findings, most of them prose that described a slightly
better system than the one that exists:
- The README said composition was both "not yet validated" and 24/28.
- The routing headline read as a measured rate. The prompt was tuned on the same 26 cases over five rounds, three of which failed the zero-false-routine gate. evals/README.md now has the round history, says there is no held-out set, and has a published-results table that a test keeps in sync with the recordings.
- The 75% composition bar was justified by a tone judgment the deterministic grader does not make; the rationale is rewritten.
- About sixty review-finding IDs had crept back into code comments and `--help` text; they are gone, and the explanations stay.
- Phase D slice names in code (D2, D4, D5) collided with the design principles D1 to D10; code now names the stage instead.
- Public docs linked into a private workspace. The Phase C entries are compacted, per this file's own standing practice, which had not been followed.
- A "Sample drafter output" section shows a real draft and a real briefing card.
- ADR-006 records the eval decisions that had no ADR: the per-stage bars, manual capture instead of live traffic, and deterministic grading without a judge.
- `test_readme_claims.py` now also fails if the README stops citing a published eval result or calls a suite "not yet validated". This is the half of the README-status drift that can be checked mechanically.

### Phase D gate: eval integrity

- **The zero-tolerance gates are tested.** Routing fails its gate on any false-routine case, whatever the pass rate. Deleting that check left every test green. The decision now lives in `routing_gate`, and a CLI test fails if a fake router answering routine everywhere passes at `--min-pass-rate 0`.
- **The composer's decline gets the same treatment.** The 3 cases that withhold a detail only the candidate has could all have come back as invented drafts and the suite would still have passed at 25/28. `composition_gate` now fails any run where one of them is drafted rather than asked. The committed recording asks in all 3.
- **A hedged "routine" is malformed.** A routine answer that also gives a reason or considerations, or names no intent, used to parse, and `followup` drafted from it. `RoutingDecision` now rejects it, so it becomes a parse failure with no draft, and the grader checks the same shapes. None of the 26 recorded answers has this shape.
- **Published numbers are pinned.** `tests/test_published_results.py` replays every committed recording and fails if a suite no longer lands on 27/33, 21/25, 26/26 or 24/28.
- **`--record` with no key refuses.** It used to record the placeholder stub over the hand-captured responses, which only git could undo. It now exits 2, and `RecordingClient` refuses a stub on its own as well.
- **`jscc costs` shows failed calls.** A call that raised mid-request (possibly billed) was dropped from the report. A ledger containing only failures printed "no LLM calls recorded yet". Failures now have their own column and a note under the table.
- A missing recording's error message named `eval jd_extraction` for every suite. It now names the suite being replayed.

+14 tests (607).

### Phase D gate: safety hardening

Two cold reviews ran at the end of Phase D, one adversarial and one reading the repo as an outside reviewer would. This slice closes their safety findings. Every replay (27/33, 21/25, 26/26, 24/28) is unchanged, so no recording was invalidated and no capture round was needed.

- **One call path to the model.** Extraction, scoring, routing and composition each carried a copy of sanitize, verify, meter, call, check for truncation. They now call `jscc/stage_call.py`'s `call_stage`, the only module that calls the model client. The egress test had listed only the top level of `jscc/`. That missed nothing until this morning's CLI split moved every command into `jscc/cli/`, after which a direct model call there would have passed. The scan now walks subpackages, with a test that it finds a nested caller.
- **Contact names are redacted.** The sanitizer could substitute a stored contact's name with a role token, and the README, D8, the threat model and two docstrings said it did. No caller ever passed it the names. Routing and composition now load the application's contacts themselves. Full names only: a bare first name would also rewrite ordinary words that contain it, so a contact mentioned by first name still goes out. T1 and D8 now say so.
- **Redaction before serialization.** Each prompt was serialized to JSON before redaction. `json.dumps` escapes non-ASCII, so a danger-list name with an accent was compared against its escaped form and went out unredacted. A redaction could also consume a quote and hand the model invalid JSON. String fields are now redacted one by one, and the prompt is serialized from the verified payload, byte-identical to before for every recorded case. Record ids that look like digit runs are left alone, but only when UUID-shaped.
- **The drafter no longer sees posting text.** Routing and composition sent the whole application, including the raw posting. The posting is irrelevant to whether a follow-up is routine, and it is the one input a stranger controls, aimed at the component with a zero-tolerance gate on answering "routine". `source_raw`, `source_url`, `extracted_jd` and `fit_rationale` are now sent as their empty defaults, which is exactly what every fixture already had.
- **Older databases.** The schema bump that added `llm_calls.error` created the column only in new databases, then stamped every database as v4. On an older file, every metered command failed after the call was billed. Opening a database now adds missing columns before stamping the version, including on files that already carry the false stamp.
- **Terminal output.** Only `report` stripped control characters. Everything a command prints now goes through one helper that removes C0 and C1 controls, and a test fails if a command calls `click.echo` directly.
- **Smaller.** Synthetic mode always uses the example profile, even when a private one exists (`resolve_profile_path` now requires the mode). The example profile gained generic style samples, and `followup` refuses a profile without any before making a billed call. An empty draft body with no `needs_input` is a parse error rather than an empty email.

ADR-005 has a second addendum for the single call path. +21 tests (593).

### Phase D gate, backlog sweep: `cli.py` split into a `jscc/cli/` package

Walkthrough finding W-13 (Phase C -> D gate) had asked for a split "before Phase D adds a drafter command on top". Phase D added `route` and `followup` and the split didn't happen, so the file went from 1,068 to 1,422 lines. The backlog sweep that opens the Phase D gate caught it. The code moved without changes: `_app` (root group), `_common` (the exit-code contract, mode/DB open helpers, `--now` parsing), and one module per command family (`admin`, `ingest`, `agents`, `eval_cmds`). `jscc.cli` still exports `cli`, `main` and the exit codes. The only test changes are mock targets, which now name the module where each command looks the patched name up. 572 tests pass.

### D4b: composition manual capture, 24/28 (86%)

All 28 composition cases captured through my own Claude.ai chats (Sonnet 4.5, `claude-sonnet-4-5-20250929`, one fresh chat per case) and recorded to `evals/composition/recorded.json`; none of it is proxy output. Replay: **24/28 (86%)** against `COMPOSITION_PASS_THRESHOLD = 0.75`, which passes. All 3 escalation cases returned `needs_input` correctly. The 4 failures are all `style_reuse` (a verbatim style-sample phrase): `interview-availability-confirm`, `logistics-video-link`, `cadence-nudge-after-onsite`, `cadence-nudge-applied-quiet`. A full 28-case Sonnet proxy run beforehand (25/28, advisory only) had predicted 3 of the 4.

Three defects the grader does not check, seen while reading the completions: `logistics-video-link` says "Tuesday, September 23" (it is a Wednesday, and the history names no weekday); `thank-you-hm-specific-topic` says "yesterday" and `thank-you-sparse-notes` says "last week", both invented relative dates. Logged as a fast-follow (a grader check for weekdays that do not match the date and for relative-date words), deliberately non-blocking: end-to-end delivery comes first. Not fixed here, so the 86% is unchanged by them. One capture round of a 28-case suite is a band, not a point.

### Routing round 5: 26/26, zero false-routine

Re-captured the whole routing suite through my own Claude.ai chats (Haiku, one fresh chat per case) against the tightened wording and the corrected `routine-recruiter-ack` fixture; `evals/routing/recorded.json` was reset first, so all 26 recordings are round-5 completions and none come from proxies. `eval routing --replay`: **26/26 (100%)** against the 0.85 bar, no false-routine. `non_routine-dietary-needs-unknown`, the round-4 false-routine, now goes to a human with the unrecorded dietary detail named. All 12 routine cases stayed routine. A few completions reasoned from the chat's real date; none changed a verdict, so no "as of" date was added to the fixtures.

### Routing wording tightened after round 4

A next action that only names a topic ("Confirm attendance and lunch needs") is now
a task, not a recorded answer. Haiku proxy runs: dietary case 5/5 non_routine, both routine
anchors held. `routine-recruiter-ack` then went non_routine 4/4 because its next action
("Reply confirming interest and availability") named availability nobody had recorded, the
same shape as `availability-unrecorded`; the fixture's next action is now "Reply confirming
interest" (a labeling correction, decided after seeing the result). Proxy output is advisory;
round 5 with real chats is the test.

### Routing round 4: 23/26, automatic fail on one false-routine

First real (Claude.ai chat, Haiku) round against the missing-information prompt.
`non_routine-dietary-needs-unknown` was classified routine: the next action
"Confirm attendance and lunch needs" was read as recording the answer. Two
routine cases were called non_routine (safe direction; Haiku reasoned from the
chat's real-world date). The proxy runs had passed the dietary case 3 of 3 and
the whole suite 26/26 with zero false-routine, so a proxy-only sign-off would
have shipped the false-routine: the real round is the only validation, and the
proxies are a filter for whether it is worth running. Details in
`evals/README.md`. Next: tighten the rule so a next action that merely names a
topic is a task, not an answer (only an answer stated in the notes or next
action counts), proxy-iterate, then a full round 5 (the prompt change
invalidates all 26 recordings). `evals/routing/recorded.json` holds round 4 as
evidence until round 5 replaces it.

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
that needs an unrecorded detail away from it. Decided 2026-09-19: a
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
a defect. Decided 2026-09-19:

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

`grade_composition` is no longer presence-only. Decided
2026-09-19: deterministic checks in code, tone not graded (a hand-graded tone
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
  (the LLM-judge rubric the original plan named -- tone match, reference to
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

## Phase C — fit scoring (C1–C3, closed 2026-09-12; Phase C → D gate 2026-09-12)

The second LLM stage: a scorer that sees both the extracted fields and the raw
posting (design principle D9), its eval suite, its first manual-capture round, and
the cost report that finally reads the ledger back. Per-slice detail is in git history.

**The build**

| | |
|---|---|
| C1 | Fit-scoring eval suite. Each case grades a score band, not an exact score, because a fit judgment has no single right answer. Rationale is checked for presence only. |
| C2a | Scoring prompt (Sonnet) and call path, behind `StubScoringClient`: there is no API key for this project. |
| Sizing | Suite resized 10 → 25 cases before any capture effort was spent, from the binomial standard error at the 80% bar. Extraction had learned the same lesson mid-round in Phase B. |
| C2b | `eval --manual`: prints each prompt to paste into a chat and records the pasted reply, keyed exactly as a live recording would be. Round 1: 21/25 (84%), above the bar. Three misses sat just outside a band on comp/level judgment. The fourth (case-14) is a real prompt finding: an adjacent higher title reads as "far outside" the target role. Deferred to the next scoring-prompt change, so one re-capture covers it. |
| C3 | `jscc costs`: per-feature cost, p50/p95 latency, and a check that flags any recorded cost that no longer matches its model's published rate (the Phase B pricing error, found by hand, is the shape it catches). |

**What the Phase C → D gate changed**

- **An earlier "accepted residual" was wrong.** The fetcher checked one DNS resolution and then let the HTTP library resolve again to connect, so a rebinding domain could pass the check and connect to a private or cloud-metadata address. An earlier gate had rated this "reasoned, not exploited" and documented it. This gate found the concrete path. Every request, including each redirect hop, is now pinned to the addresses already validated. The lesson became a line in the gate method: re-derive the severity of a carried-forward residual, not just its presence.
- **A model score of `NaN` or `9001` would have been stored.** `FitResult.score` is now bounded to 0–100, which also rejects non-finite values.
- **A call that failed mid-request left no ledger row**, although the provider may already have billed it. It now writes a marked row. The schema bump that added the column turned out, at the Phase D gate, to stamp older databases without migrating them. Fixed there.
- **Kept, deliberately:** trimming unused profile fields from the scoring payload would invalidate every recording, so it waits for the next scoring-prompt change. The walkthrough flagged CHANGELOG length for the third time; Phase B was compacted the same day.

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
of 2026-09-12; the full review notes are private working files, and
[docs/gate-reviews.md](docs/gate-reviews.md) has the method and worked examples. The entire
carried-forward A2/A9/A10-era backlog (walkthrough #5 ADR-001 framing fixed,
#6 coverage badge killed, #7 CHANGELOG split killed the first time, then
replaced by compacting one phase behind — plus the `update_application`
field-whitelist gap and three low-severity sanitizer/report findings) closed
the same day, caught by the first backlog sweep (a standing step that opens every gate) rather than riding
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
killed), #7 (CHANGELOG split, killed, then replaced by compacting one phase behind), and L-json-default-sanitizer-1 (fixed once Phase C's fit-scoring
payload gave it a real trigger). Caught by the first backlog sweep
after sitting untouched across two phase boundaries — nothing here rides
silently through a third one now that the sweep exists.
