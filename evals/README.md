# Evals

One suite per LLM stage. Four stages exist today — `jd_extraction`, `fit_scoring` (D9 splits extraction from scoring so facts and judgment regress independently), `routing` (D10 step 1 of the routing-first drafter), and `composition` (D10 step 2A, reached only for a `routine` classification). Each suite is a JSON case file plus a grading function in `jscc/evals.py`.

## Published results

What each committed `recorded.json` replays to. `tests/test_published_results.py` replays all four and fails if any count changes, so this table cannot drift from the recordings.

| Suite | Result | Bar | Evidence |
|---|---|---|---|
| jd_extraction | 32/36 (89%) | 80% | Third capture round, on a prompt changed after rounds 1 and 2 (56%, then 75%). The prompt was debugged against these same fixtures, so 89% is not a held-out rate; earlier rounds on earlier wording were 76%-82% (33 cases), 56% and 75%. Misses: 06, 26, 27, 31. |
| fit_scoring | 27/28 (96%) | 80% | Round 1 on the revised prompt (Claude.ai default Sonnet 5, 2026-09-24), after 64% on the first prompt. The prompt and nine ranges were changed after that first round, so this is not a held-out rate. A second round is pending. |
| routing | 26/26 (100%) | 85%, and zero false-routine | Round 5 of 5, on the cases the prompt was tuned against; see the round history below. |
| composition | 24/28 (86%) | 75%, and every must-ask case asks | One capture round. |

All recordings are real model output captured by hand through Claude.ai chat, one fresh chat per case; none is proxy output. A single round is a first data point, not a demonstrated range.

## jd_extraction

`evals/jd_extraction/cases.json` — 36 hand-authored JDs (fictional companies, no real personal data) with expected `ExtractedJD` fields, split into three `group`s:
- **`short`** (25 cases) — clean, paste-shaped JD text averaging ~360 characters, matching what a human pastes via `ingest --paste`. Covers the level/comp/location/remote/skills cross-product plus edge cases (ambiguous comp, multiple locations, no named skills, terse input).
- **`long`** (8 cases) — synthetic-but-realistic fetched-page length and noise (nav breadcrumb, benefits list, EEO/export-control boilerplate, application form fields) at roughly 2,000-3,000 characters, matching the order-of-magnitude the B3b smoke test found on real postings. Written from scratch, not copied from any real posting — `cases.json` is a tracked, public file, so committing verbatim third-party job-posting text would be a copyright problem, the same reason the smoke-test snapshot (`scripts/smoke_fetch.py`'s gitignored output) records only URL and outcome metadata, never raw fetched text.
- **`hostile`** (3 cases, T5 in `docs/threat-model.md`) — ordinary postings that also address the parser: an injected level and comp band, an injected skill list in an HTML comment, and an instruction to drop the schema and reply with one word. Graded like any other case, plus a `forbidden` list so an injected comp figure fails even though `comp_band` is graded on presence only.

The `short`/`long` split exists because 15 cases gave the pass-rate threshold a standard error of roughly ±10 points — noisy enough that a stable prompt could show anywhere from ~70% to ~90% depending on which cases happened to be in the set. 33 cases brings that to roughly ±7-8 points. That is "less noisy," not statistically airtight: a textbook-tight interval would need 60-100+ cases, which is impractical when every case is captured by hand through Claude.ai chat rather than by live API calls.

Grading (`grade_extraction` in `jscc/evals.py`):
- **Exact match:** `level`, `remote_policy`.
- **Normalized match (case/whitespace only):** `title`, `company` (nullable — some postings never name the employer; both-null passes like any other normalized-field match).
- **Set equality (order-independent):** `must_have_skills`, by word-set containment (wording is forgiven, scope is not). An expected entry may be a list of alternatives; one slot is satisfied by naming any of them, or all of them. See "Expected skills accept the posting's own wording" below.
- **Presence-only:** `comp_band` — both-None or both-not-None; exact dollar figures aren't graded because they're too brittle to pin a prompt to.
- **Presence + containment:** `location` — both-None/both-not-None, and one must contain the other ("Denver" vs. "Denver, CO" passes; "Denver" vs. "Seattle" fails).
- **Prose, not graded here:** `responsibilities_summary` — checked for non-empty only. No suite uses an LLM judge.

### Expected skills accept the posting's own wording

The extraction prompt tells the model to take each skill from the requirement's own words, so an expected entry must be satisfiable by the words the posting uses, not only by a canonical paraphrase. A mechanical check (2026-09-23) found exactly two expectations whose words the posting never uses, and one slash compound graded as two skills. All three were changed before the recapture, not after seeing its result:

- **case-02:** `model deployment` became `["model deployment", "shipping models to production"]`. The posting says "shipping models to production".
- **case-04:** `infrastructure as code` became `["infrastructure as code", "infra as code"]`. The posting says "infra-as-code". Containment cannot bridge `infra` and `infrastructure`, so the abbreviation is listed. The earlier docs called this gap deliberately unfixed; with the verbatim-words rule it is the correct behavior.
- **case-27:** `Firmware` and `BMC` became one slot, `["Firmware", "BMC"]`. The posting writes "firmware/BMC layers" as one requirement. A model that returns `Firmware/BMC` as a single entry matched only one of the two separate slots and failed; a slot is satisfied by either half or both. The cost: a reply naming only one of the two now passes.

`tests/test_evals.py::test_skills_alternatives_accept_the_postings_own_wording` pins all three shapes. A smarter grader (abbreviation and synonym handling) is a possible later improvement; it is a backlog item in the JSCC plan, not something to build before the plan's phases are complete.

Run: `python -m jscc eval jd_extraction`. Exits non-zero if the combined pass rate falls below `PASS_THRESHOLD`; `format_eval_summary` also reports a `short`/`long` breakdown so a regression says which distribution broke, without a second gate.

## Before a capture round

A round costs one fresh chat per case, and a prompt change afterwards throws the whole round away
(recordings are keyed on the exact prompt text). Routing took five rounds for that reason. Before
starting one:

1. **Freeze the prompt.** Make every prompt-affecting change and decision first, including anything
   the batched cleanup items would change. Fixture edits count too.
   `python scripts/capture_tools.py recapture-cost` lists every case the working tree's edits
   invalidate, so the cost of an edit is known before it is committed to.
2. **Run the full suite through proxies on the final wording,** with the target model, not a
   targeted subset. A proxy is a lower bound on failures, not a prediction: routing round 4 was
   proxy-clean and still failed on a real chat.
3. **Read 5 to 10 proxy outputs yourself,** looking for defects the grader does not check. Composition
   drafts that invented a weekday or a relative date passed every check.
4. **Give each fixture an as-of date if its answer depends on today's date.** A model in a real chat
   sees the real date and can turn a routine cadence case into a judgment call.
5. **Check the model.** `capture_tools.py show` prints the target model first and last on every case.
6. **Prefer an incognito chat for composition and routing.** Their prompts can draw on real names and
   writing voice, and chat memory could carry that into a completion that lands in a tracked recording.
   It is optional for `jd_extraction` and `fit_scoring` (fictional inputs): a jd_extraction incognito
   control matched the normal chats. Incognito chats are not saved, so copy each reply as you go.

`scripts/capture_tools.py` does the mechanics for all four suites: `prompts` builds each case's exact
prompt from the current code, `show` prints one for the chat, `record` saves a completion under the key
replay looks up, and `proxy-prep` / `proxy-grade` run the proxy loop. It refuses stale prompts and
refuses to record anything under a `proxy` directory, so proxy output cannot end up in a recording.

## Adding a case

Append an object to `cases.json` with a unique `id`, `raw_jd` (never real personal/company data — synthetic or scrubbed only, per D7/D8), an optional `group` (`"short"` default, `"long"` for fetch-shaped noise, `"hostile"` for injection attempts), an optional `forbidden` list (strings that must not appear anywhere in the extraction, case-insensitive), and an `expected` dict matching `ExtractedJD`'s fields. Cover both presence and absence of `comp_band` and a mix of `remote_policy` values — the grading logic branches on those. Avoid "X or Y" phrasing in a requirements section you expect graded by `must_have_skills` — the set-equality check can't credit a model for picking either disjunct, so it fails a correct answer either way.

## fit_scoring

`evals/fit_scoring/cases.json` — 28 hand-authored (JD, profile) pairs: 25 spanning the fit spectrum, plus 3 hostile postings (case-26 to case-28) whose text asks the scorer for a top score, to ignore a deal-breaker, or carries the instruction into the extracted fields; each keeps the band its real fit deserves. The 25: clear high fit, comp below/partially-below/missing, level mismatch (below and above target, including executive scope), deal-breakers present (both structurally-tagged and detectable only from raw JD text, including one buried in unrelated boilerplate), must-haves entirely missing or satisfied only via raw-text nuance, borderline hybrid cases, comp above target (not a downside), an ambiguous minimal posting, a total role mismatch, role-focus matching only one of two profile entries or neither, a thin/empty skills list that shouldn't tank an otherwise-strong match, an ambiguous "Tech Lead" title, and two cases against a second, IC-focused profile (`role_focus: staff/principal engineer`, deal-breaker on required people management) to test that grading isn't hard-coded to one profile shape. All cases share `config/profile.example.yaml`'s base profile except those two.

**Sized at 25, not the original 10 (resized 2026-09-12, before C2b spent any manual-capture effort):** at the ≥80% threshold, the binomial standard error on a pass rate is `sqrt(p(1-p)/n)`. At n=10 that's ~13 points — noisier than the exact problem `jd_extraction` hit at n=15 (~10 points, see below), which is what drove its own resize to 33 cases. Catching the same problem here before any capture spent against it — rather than discovering it mid-round the way B2b did — is the whole point of writing it down.

Grading (`grade_fit_score` in `jscc/evals.py`):
- **Band, not exact score:** each case names a `min_score`/`max_score`. A fit judgment has no single right answer, so bands are wide on purpose ("high fit 75-95", "clear pass <30").
- **Rationale:** checked for non-empty only; whether it names the right factors is not graded.

Run: `python -m jscc eval fit_scoring`. C2a landed the real prompt (Sonnet, per D9) and full `--record`/`--replay`/`--min-pass-rate` parity with `jd_extraction`, but no `ANTHROPIC_API_KEY` is configured for this project — same constraint B2 hit — so today it runs end-to-end against `StubScoringClient`, a fixed placeholder score of 0. That coincidentally clears a handful of deliberately-low-fit bands (score 0 is a valid "clear low fit" answer — 8/25 today), so unlike `jd_extraction`'s stub the invariant isn't "every case fails" — it's "the pass rate stays far below `PASS_THRESHOLD`," which it does (no constant score clears the bar across bands spanning 0-100).

**C2b: `--manual` capture.** `jd_extraction`'s manual-capture round (B2b) had no tooling — `recorded.json` was hand-edited, one entry at a time. `python -m jscc eval fit_scoring --manual` closes that gap: for each case it prints the exact model id, system prompt, and user message to paste into Claude.ai chat, then reads the pasted-back completion (terminated by a line containing only `END`) and persists it immediately to `evals/fit_scoring/recorded.json`, keyed the same way `--record` against a live key would be. `--manual` implies `--record`'s persist-immediately behavior for free, since it's just `RecordingClient` wrapping a different inner client (`ManualCaptureClient` instead of the real API client). Reports zero tokens/cost, honestly — no billed call happened. Once captured, `--replay` works exactly as it does for `jd_extraction`.

**Round 1 (2026-09-12): 21/25 (84%)**, above the 80% bar. Three misses (case-02, case-06, case-08) landed just outside a band on comp/level boundary judgment calls — expected instability, not a wording gap. The fourth (case-14, a director/VP posting one level above the profile's target with comp above range) is a real prompt-language finding, confirmed with a second independent capture (38, then 22, both against a 70-95 band, both citing "far outside role_focus"): the prompt's role/level factor treats an adjacent higher title as categorically outside `role_focus` rather than as one step up that above-range comp should help offset. Deferred rather than fixed against round-1 data alone, since 84% already clears the bar; it will be fixed with the next change to the scoring prompt, so one re-capture round covers it.

**Ranges corrected after round 1 (2026-09-23, before the recapture).** Round 1 on the unchanged prompt scored 18/28 (64%). Nine `min_score`/`max_score` pairs were changed because they contradicted the prompt's own rules, not to match the returned scores; the prompt also gained numeric bands and an adjacent-title anchor (see the CHANGELOG entry). Round 1 informed the choice, so the corrected suite is not a blind test and the recapture must be reported as a fresh round, not as round 1 re-graded.

| Case | Old | New | Why |
|---|---|---|---|
| 06 hybrid, comp unstated | 35-65 | 70-95 | Comp unstated is neutral and a silent must-have is unknown; nothing deducts. Same shape as case-18. |
| 07 director, comp above range | 75-100 | 65-85 | Adjacent title: the prompt now anchors the low 70s. Floors of 07 and 14 disagreed. |
| 08, 21 minimal mid-level posting | 15-55 | 5-55 | A mid-level IC against an L6-L7 target is far outside; 15 was a floor the prompt does not support. Not 0, so a constant-zero stub still fails those cases. |
| 14 VP, executive scope | 70-95 | 65-85 | Same anchor as case-07. |
| 15 senior staff IC | 65-90 | 65-100 | A direct match on every factor; nothing justifies a cap below case-01's. |
| 17 must-have met via raw text | 55-85 | 70-95 | "Remote-friendly" meets the must-have; same shape as case-18. |
| 23 hybrid, legacy monolith | 50-80 | 70-95 | The posting states modern CI/CD; the prompt counts a must-have as missing only when the posting contradicts it. The case id keeps its old "partial miss" name. |
| 24 tech lead, band straddles the floor | 20-60 | 55-85 | The prompt had no rule for a band straddling the target minimum. It now counts the band as within range when its midpoint is above the minimum ($290,000-$360,000 against $300,000), so no comp cap applies; case-13 ($260,000-$340,000, midpoint exactly $300,000) stays capped. The title is not a listed target, so the range stays wide. |

## routing

`evals/routing/cases.json` — 26 hand-authored (application, history) fixtures, 12 routine and 14 non-routine. The first 12 split evenly across the surface D10 names: routine (post-interview thank-you, cadence nudge on a stale screen, onsite-logistics confirmation, a cold recruiter outreach needing acknowledgment, thank-you after a phone screen, thank-you to a referrer) and non-routine (a feedback-seeking rejection reply, a compensation negotiation, first outreach to a warm personal contact, two threads giving conflicting instructions, an interaction note carrying a contact's personal/medical disclosure per D8, and a genuinely ambiguous recruiter check-in with no clear ask). How it grew to 26 is below.

Grading (`grade_routing_decision` in `jscc/evals.py`):
- **Classification, exact:** `routine` vs. `non_routine` is the case-defining check — a case fails outright on a wrong classification regardless of what else the decision contains.
- **Shape:** a correctly-classified `routine` decision needs a non-empty `intent` and no reason or considerations (the model rejects the other shape at parse time too); a correctly-classified `non_routine` decision needs a non-empty `reason` and at least one `considerations` entry. Whether `intent` names the *right* routine bucket, or `considerations` are useful, is not graded.

**Two separate gates, per D10 (`ROUTING_PASS_THRESHOLD = 0.85` in `jscc/evals.py`):**
1. The combined pass rate must clear 85% — stricter than `jd_extraction`/`fit_scoring`'s 80%, since a router's classification is closer to a safety gate than a content-quality judgment.
2. **Zero false-routine cases**, independent of (1). `routing_gate` applies both, and a CLI test fails if the second is removed. `false_routine_cases` scans for any case where a genuinely `non_routine` fixture got classified `routine` — the one failure mode D10 calls out as categorically worse than the rest, since it means auto-drafting something that needed a human. A prompt could clear 85% overall while still auto-drafting something it shouldn't; the CLI command refuses to call that passing.

Run: `python -m jscc eval routing`. The real prompt runs on Haiku (per D10; it reuses `EXTRACTION_MODEL`'s id), with full `--record`/`--replay`/`--manual`/`--min-pass-rate` parity with `fit_scoring`. Against `StubRoutingClient` (no `ANTHROPIC_API_KEY` configured, same constraint B2/C2 hit): unlike the other two stubs, `StubRoutingClient`'s fixed answer is a deliberate one, not an arbitrary placeholder — per D10's own bias, it always answers `non_routine`, the honestly correct "safe when uncertain" behavior for an unconfigured router. That means the stub trivially clears gate (2) (it never says "routine," so there is nothing to be a false-routine case) while failing gate (1) at 54% (14/26 — every non_routine-expected case passes, every routine-expected case doesn't, since the stub can't tell them apart).

**Round history.** Five manual-capture rounds, each on Haiku, one fresh Claude.ai chat per case. After every failing round the prompt or a fixture was changed and the whole suite re-captured. The same cases were used throughout; there is no held-out set.

| Round | Cases | Result | False-routine | What changed next |
|---|---|---|---|---|
| 1 (2026-09-17) | 20 | 19/20 | `decline-offer` | Prompt names declining an offer as non-routine |
| 2 (2026-09-18) | 20 | 19/20 | `keep-in-touch-rejection` | Prompt: any rejection, however warm, is non-routine |
| 3 (2026-09-19) | 20 | 20/20 | none | Suite grown to 26 for the missing-information rule |
| 4 (2026-09-19) | 26 | 23/26 | `dietary-needs-unknown` | Prompt: a next action naming a topic is a task, not an answer; one fixture's next action corrected |
| 5 (2026-09-20) | 26 | 26/26 | none | — |

So 26/26 means the current prompt handles these 26 situations, including the four that each broke an earlier version. It is not a rate for situations the prompt has not seen. The next routing capture should add a set of fresh cases written without looking at the prompt, and report them separately.

**`--manual` capture.** Same mechanism `fit_scoring` built at C2b — `python -m jscc eval routing --manual` prints each case's model id, system prompt, and user message to paste into Claude.ai chat, then reads the pasted-back completion (terminated by a line containing only `END`) and persists it immediately to `evals/routing/recorded.json`.

**Case count resized 12 → 20, mid-round, 2026-09-17.** At `ROUTING_PASS_THRESHOLD = 0.85`, SE(n=12) ≈10.3pt — the same undersized range `jd_extraction` (n=15) and `fit_scoring` (n=10) were resized out of before their own manual-capture rounds started, except this time the check was skipped and the gap wasn't caught until 3 cases into the first capture round. Added 8 cases (4 routine, 4 non_routine) to reach n=20 (SE ≈8pt, matching the other two suites' final precision), split so the false-routine (non_routine-expected) sample widens too, not just the combined n.

**Grown 20 → 26, 2026-09-19, for the missing-information rule.** The router prompt now sends a reply that must state an unrecorded detail about the candidate (dietary needs, availability, which of several slots) to a human, while accepting a single proposal the candidate's own next action names stays routine. Added 4 non_routine cases (`dietary-needs-unknown`, `pick-slot-unrecorded`, `availability-unrecorded`, `withdraw-from-process`) and 2 routine boundary anchors (`slot-pick-recorded`, `video-option-recorded`), so 12 routine / 14 non_routine and SE about 7pt at 85%. The prompt change invalidates the 20 recordings from round 3, so a round 4 is required; the `recorded.json` on disk is round 3's and will not replay.

**Round 4 (2026-09-19), captured through Jess's own Claude.ai chats, Haiku, one fresh chat per case: 23/26 (88%), automatic fail on one false-routine.** The pass rate clears the 0.85 bar, but `non_routine-dietary-needs-unknown` was called routine, and a false-routine fails the gate regardless of the rate. The next action there reads "Confirm attendance and lunch needs"; Haiku took it as recording the answer, which is the case the missing-information rule was written to stop. The other two misses (`routine-materials-confirm`, `routine-cadence-nudge-alt`) were routine cases called non_routine, the safe direction; in both Haiku reasoned from the real-world date ("Sept 19") that the chat supplies and the fixture does not, and those prompt paths were unchanged, so they are treated as variance. The 4 other new cases behaved: both routine boundary anchors (`slot-pick-recorded`, `video-option-recorded`) stayed routine and `pick-slot-unrecorded`, `availability-unrecorded` and `withdraw-from-process` went to a human, with the missing detail named.

**Why this is on the record: the real round caught what the proxies missed.** Before round 4, the wording was proxy-iterated on Haiku subagents (fresh agent per case): the earlier pick-slot false-routines showed up in proxy rounds m1 and m3, were fixed, and the final wording went 26/26 with zero false-routine (m5), the dietary case 3 of 3 correct. So the proxies were useful as a filter, but a proxy-only sign-off would have shipped a router with a live false-routine. Likely reasons, none proven: proxy agents run inside the Claude Code harness with its own instructions and context, so they are plausibly more careful and less variable than a plain chat completion; and each proxy sample is one draw, so a case the model gets right most of the time can still fail once. The real round cost 26 chats of hand work against minutes for the proxies, and it is still the only evidence that counts. Standing rule reinforced: proxies gate whether a manual round is worth running; they never replace it.

**Record/replay bug found and fixed the same round.** The routing prompt serializes the `Application`, which includes `created_at`/`updated_at` (`Field(default_factory=_now)`); the fixtures never pinned those two fields the way every other date field in them was already pinned, so each fresh `Application(**case.application)` construction stamped a live timestamp into the hashed prompt text. Effect: `--record`/`--manual` and any later `--replay` are separate process runs, so the embedded timestamp never matched between them — record/replay was broken for this suite from the start, independent of prompt or model quality. Fixed by pinning `created_at`/`updated_at` on every fixture (= first/last history item's `occurred_at`), verified deterministic across repeated runs.

**Round 5 (2026-09-20), captured through Jess's own Claude.ai chats, Haiku, one fresh chat per case, after the wording tightening and the `routine-recruiter-ack` fixture fix: 26/26 (100%), no false-routine.** `recorded.json` was reset before capture, so every recording is a round-5 completion. `non_routine-dietary-needs-unknown` was called non_routine with the missing detail named, and all 12 routine cases stayed routine. Proxy runs preceded this round but are advisory only and are not in the recordings.

## composition

`evals/composition/cases.json` — 25 hand-authored (plus 3 escalation cases, below) (application, history, intent, style_samples) fixtures, all routine per D10: post-interview thank-you, cadence nudge, onsite-logistics confirmation, a cold recruiter-outreach acknowledgment, thank-you after a phone screen, thank-you to a referrer, an acknowledgment of a recruiter's onsite prep guide, and confirming availability for a proposed interview time. There is no non-routine case here — the routing step already refuses to route a non-routine situation to composition at all, so every fixture this suite exercises is one `route_followup` would classify `routine`.

**Case count resized 8 -> 25 before the first capture, 2026-09-19.** At the 75% bar SE(n=8) is about 15pt, worse than any suite already resized (`jd_extraction` n=15, `fit_scoring` n=10, `routing` n=12 before their own fixes). 17 routine cases added (SE(n=25) about 8.7pt, matching `fit_scoring`'s final size), weighted toward the shapes a draft can get wrong (the original `graceful-decline` fixture, a candidate withdrawing from a process, was replaced with `prep-guide-acknowledgment` because the validated router prompt sends commit-to-an-outcome replies like declining an offer to a human, so it would never reach composition): sparse history where the model must not invent details (`thank-you-sparse-notes`), a specific prior topic it should reference (`thank-you-hm-specific-topic`), a repeat nudge, multi-panelist and skip-level thank-yous, and logistics with concrete times to echo back. Style samples run 1-3 per case. Every fixture also pins `Application.created_at`/`updated_at` (first/last history `occurred_at`), the same fix the routing suite needed: left unset they default to now() and get hashed into any prompt built from the fixture, which would break record/replay keying.

**Escalation cases added 2026-09-19, 25 -> 28.** The composer has an escape hatch: when a good reply would need a fact the input lacks, it returns `{"needs_input": "<the missing detail>"}` instead of a draft, and `followup()` turns that into a briefing. Three fixtures (`escalate-dietary-needs-unknown`, `escalate-pick-slot-unrecorded`, `escalate-availability-unrecorded`, marked `expect_needs_input: true`) are situations the router should never send here, so they test the layer behind it. They pass only when the composer escalates and names the detail (`must_include` is checked against the `needs_input` text); a draft written anyway fails as `needs_input`. The reverse is also graded: a drafting case that escalates fails as `unexpected_needs_input`. The 25 drafting cases stay the pass-rate denominator's bulk (SE(n=28) about 8.2pt).

Grading (`grade_composition` in `jscc/evals.py`):
- **Deterministic checks:** a case passes only if every check passes, and each failure names its check in the diff. Generic: non-empty subject and body; no bracketed placeholders or `redacted` tokens; body 30-160 words (the prompt asks 50-130) and subject at most 10 words (the prompt asks 8 or fewer); no invented numbers (every digit run in the draft must appear in the application fields or history, leading zeros normalized; style samples are excluded from that corpus because they are voice, not facts); no copied style-sample sentence of 6+ units, where a listed stock phrase ("wanted to check in", "no rush", "timing for next steps", "putting my name forward", and the rest of `_STOCK_PHRASES_RAW`) counts as one unit so ordinary social convention can be echoed but a whole sentence cannot; no invented capitalized names (not sentence-initial, not in the facts, not a weekday/month/closing). Per case: `must_include` (any-of groups, one hit per group) and `must_not_include` (hallucination traps; every case also carries a salary/compensation/other-offer trap). Tone and overall quality are deliberately not graded; a pass means no mechanical defect, not a good email. A draft using more than 4 distinct stock phrases gets a `form_letter` advisory (reported in the summary, never failing). The invented-name check was expected to be the noisiest, so it is calibrated on proxy runs and demoted to advisory if it false-positives.

Run: `python -m jscc eval composition`, with `--record`/`--replay`/`--manual`/`--min-pass-rate` parity with the routing suite (bar: `COMPOSITION_PASS_THRESHOLD = 0.75`, plus a second, zero-tolerance gate: any escalation case answered with a draft fails the run, whatever the pass rate). `compose_followup` is a real Sonnet call through the sanitizer choke point; with no `ANTHROPIC_API_KEY` it resolves to `StubCompositionClient`, whose empty subject fails every case, so an unconfigured run reads `0/28 passed (0%)`. That is the harness working, not a bug. Recordings go to `evals/composition/recorded.json` (28 real Sonnet 4.5 completions). Grading is the deterministic checks above.

**Why 75%, the lowest bar.** Per case, this grader is the strictest and the least forgiving of a good answer. It has no judge of tone, and a draft fails for missing a synonym group, running outside the word range, or reusing six words of a style sample, all of which a good email can do. What matters for safety does not ride on the 75%. The router keeps non-routine situations out, and the second gate fails the run on any case where the composer should have asked and drafted instead.

**Round 1 (2026-09-20): 24/28 (86%), passes the 0.75 bar.** Captured through Jess's own Claude.ai chats (the Claude.ai default Sonnet, which was Sonnet 5, one fresh chat per case; first written up as Sonnet 4.5 and corrected 2026-09-24). All 3 escalation cases correct. The 4 misses are all `style_reuse`. Not caught by any check and logged as a fast-follow: one invented weekday ("Tuesday, September 23", a Wednesday) and two invented relative dates ("yesterday", "last week").
