# Changelog

Slice-by-slice arc. **Phase A is summarized** — it is closed and hardened, and
the per-slice detail is in git history where it belongs. Phase B entries keep
their reasoning, because that work is current and the reasoning is still load
bearing. Review findings are recorded here rather than in code comments.

## [Unreleased]

### Phase B -> C gate: third-pass findings, cont'd (H-6)

From the same 9/12 third-pass review (full detail:
`jscc-phase-b-rerun-gate.md`).

- **H-6** - a transient Anthropic API error (rate limit, overload,
  connection reset, timeout) propagated straight out of the SDK call with
  no exception handling anywhere between it and `ingest`/`resolve-dlq` --
  crashing with a raw traceback and, on `--paste`, losing the pasted text
  for good, since nothing durable exists yet at the point of failure. This
  is the most likely live-key failure the extraction stage has, and D6's
  contract ("produces an Application or a DLQEntry, never crashes") held
  for neither command. Both now catch `anthropic.APIError` (the SDK's
  common base for all of the above) and route it to a `FailureMode.other`
  DLQ entry, exit 3 -- `FailureMode.other` was defined and never produced
  by anything until now (see M-6). `resolve-dlq` creates no second entry;
  the one being resolved stays unresolved, same as the existing
  `ExtractionParseError` handling. Whether `source_raw` should be persisted
  before extraction is attempted, so a URL-path retry doesn't need a
  re-fetch, is a separate open decision -- not changed here.

332 tests (+2).

### Phase B -> C gate: third-pass findings (W-11, H-5, M-7, M-11, L-6)

From the 9/12 third adversarial + peer review pass (full detail:
`jscc-phase-b-rerun-gate.md`).

- **W-11** - README's Status section named three mediums (M-1, M-5, M-6) as
  open in the same sentence that claimed every medium was closed -- true
  when written, false the moment the backlog-closure commit landed hours
  later the same day. Rewritten with an "as of" date and the two real open
  highs named instead, since this list is hand-maintained and has now gone
  stale by the same mechanism twice.
- **H-5** - `evals.py`'s replay-recording key hashed only the user prompt,
  not the system prompt or model id, though its own docstring claimed
  otherwise. Verified: replacing the entire extraction system prompt with
  unrelated text replayed the same 33 recordings at the same 27/33 pass
  rate. Re-keyed on `sha256(model | system | user)`; the 33 existing
  recordings were re-keyed in place (same response text, not re-captured --
  they were already produced under the current prompt) rather than
  re-recorded from scratch.
- **M-7** - `resolve_dlq_entry`'s `application_id` UPDATE was unconditional,
  so a caller omitting the (optional, defaults to `None`) argument nulled
  out a link a prior call had set. Shipped in the M-1 fix the same morning;
  not CLI-reachable today because M-1's own idempotency guard blocks a
  second resolution. `COALESCE`d so "omitted" means "leave it alone."
- **M-11** - `report --now <past instant>` raised `detect_stale`'s
  `ValueError` as a raw traceback and exit 1, indistinguishable from the
  case's other cause (corrupt data). Since only the CLI knows whether `--now`
  was supplied, that case now raises `click.UsageError` (exit 2); an absent
  `--now` still crashes, correctly, since a future timestamp with no `--now`
  in play really is unexpected. Also closes **L-6**: `report`'s
  `load_stages` call was unguarded, unlike `validate-config`'s try/except
  around the same call; both are usage errors now.

330 tests (+4).

### Phase B -> C gate: non-blocking backlog closure (W3, M-1, M-6, L-8, L-10)

Closed most of what was left open, none of it blocking, from the 9/4 rerun
gate (full detail: `jscc-phase-b-rerun-gate.md`).

- **W3** - README's intro, eval-driven-design pitch, and Status section
  updated to reflect B2b closed (76-82% band) and Phase B substantially
  shipped, naming the gate's remaining open items as low-severity. Also
  closes **L-7**, a bare repeat of the same finding.
- **M-1** - `resolve-dlq` used to skip the entry's current resolution
  entirely, so re-running it against an already-resolved entry created a
  duplicate Application every time and re-stamped `resolved_at`. Now checks
  `entry.resolution` first and refuses to touch an already-resolved entry.
  Also sets `DLQEntry.application_id` on resolution, which was never set at
  all before. Documented gap kept at the guard: there's no "reopen" path, so
  a `wont_fix` entry can't be converted to `manual_paste` from here either.
- **M-6** - `Application.fetch_status` defaulted to `ok` on every creation
  path, including paste and DLQ resolution, so the DB claimed "fetched
  cleanly" about records never fetched. `ingest --paste` now sets `manual`;
  `resolve-dlq` maps the DLQ entry's original `failure_mode` to the matching
  `dlq_*` status.
- **M-5** - decided, not extended. The type-level choke point genuinely
  stops at `send_to_llm`, exactly as found, but extending it now would be
  building for D9/D10 call sites that don't exist yet. Decision and its
  exact revisit trigger (a second `.complete()` caller) recorded in
  ADR-005's addendum.
- **L-3, L-4** - documented as `TODO` comments at the exact lines in
  `sanitizer.py` rather than fixed: the `model`-key redaction exemption
  applies at any nesting depth, and `contains_personal` is only checked at
  the payload's top level with non-string scalars never redacted. Both are
  real but low-severity given today's flat, string-only payloads.
- **L-8** (repeat of the 9/1 gate's L-4) - a 3xx status code `fetch_jd`'s
  redirect loop doesn't follow (missing `Location`, or one requests doesn't
  call a redirect, like 304) used to fall through every status check below
  400 and get extracted as a normal page. Now caught explicitly as
  `FailureMode.blocked`.
- **L-10** - the email regex's TLD character class excluded `.` but not
  `,;:!?'"` or closing brackets, so trailing sentence punctuation right
  after an address got swallowed into the redacted span. Class widened.
- **Backlog re-checked against current code:** L-1, L-2, L-5, L-6, L-9
  remain open and untouched — none were resolved incidentally by other work.

326 tests (+4).

### B2b round 4 - two prompt fixes validated, 82% on a fresh capture

A targeted look at the four cases still failing after the grading fix (below)
found two of the four were NOT non-determinism after all -- they were
structural parsing gaps with a concrete fix:
- **Compound "and"-joined bullets dropped one of two named skills**
  (case-29: "the OWASP Top 10 and common web application vulnerability
  classes" kept only OWASP Top 10; case-31: "model evaluation and
  auditability practices" kept only one). Added a prompt rule: a requirement
  naming two distinct skills joined by "and" is two entries, not one.
- **The years-exclusion rule was bleeding across a whole comma-separated
  requirements sentence** once it saw a leading numeral (case-14: "2+ years
  as an engineering manager, prior IC background in backend systems" zeroed
  out `backend systems` too, though that clause has no numeral of its own).
  Scoped the exclusion in the prompt to the numeral's own clause, not the
  whole line.

Also fixed a real fixture bug found in the same pass: case-33's "Deep
infrastructure or platform engineering background" is a disjunctive
requirement like case-13's, not two separately-required items -- converted
to the same alternatives-list format.

**Recaptured all 33 cases fresh** (system prompt changed, and `_prompt_key`
hashes only the user text, so the old recording would silently keep serving
pre-fix completions on replay). Result: **25/33 (76%)** -- lower than the
prior 82%, even though both fixes worked exactly as intended (case-14 now
passes; case-29 and case-31 both correctly split into two skill entries).
Five previously-passing cases (case-02, -04, -06, -13, -27) regressed on
clauses neither fix touched -- direct, measured evidence that a single
capture's pass rate moves several points on model variance alone, holding
the prompt fixed. That reframes the earlier 82% as one sample, not a stable
number.

**Two more grading bugs surfaced while diagnosing the regressions, fixed
without another capture:**
- `_normalize` folded hyphens but not slashes, so "firmware/BMC" (one
  Haiku-produced token) never matched "Firmware" or "BMC" (two fixture
  entries). Now normalizes both the same way. Doesn't fully resolve case-27
  -- one actual entry satisfying two expected slots at once is a further
  design question, not solved here.
- An alternatives slot (case-13, case-33 style) only consumed the *first*
  actual entry that matched one of its options, so a model naming BOTH
  acceptable alternatives got the second one flagged as an unexplained
  extra (case-33: "infrastructure engineering" AND "platform engineering"
  both named, one wrongly counted against it). Now consumes every match --
  naming both options of one disjunctive requirement isn't a scope
  violation.

**Two fixture-wording corrections**, same root cause as the original round-1
fixture bugs, just a fresh instance: case-29's expected `"web application
security"` and case-31's expected `"model auditability"` were fixture-author
paraphrases, not the JD's literal phrasing (`"web application vulnerability
classes"`, `"auditability practices"`). Corrected to match the source text.
Re-graded the SAME round-4 capture (no new capture spent): **27/33 (82%)**.

**Remaining 6 failures, categorized -- all real, none more fixture noise:**
- case-02, case-06: genuine wording/omission variance holding the prompt
  constant (`"model deployment"` vs. `"shipping models to production"`;
  `"managing managers"` dropped entirely this round though it passed in the
  prior capture).
- case-04: an abbreviation gap the containment grader doesn't cover
  (`"infra-as-code"` vs. `"infrastructure as code"` -- no shared whole word).
- case-13: the disjunctive slot entirely unaddressed this round (neither
  `"applied statistics"` nor `"data science"` named).
- case-27: the slash-joined-compound design gap above.
- case-33: `"scaling engineering organizations"` extracted as an extra --
  consistent with the same "track record scaling..." phrasing being
  correctly excluded in case-06's fixture, so this reads as a genuine
  over-extraction, not a fixture problem.

+0 tests this pass (grading/prompt/fixture only); 322 still passing.

### B2b grading fix - 82%, DoD met against the round-1 recording, no new capture

Round 1 landed 21/33 (64%) against exact-set-equality grading on
`must_have_skills`. Before spending another manual-capture round on prompt
wording, re-examined whether the grader itself was the bottleneck --
re-graded the *same* round-1 recording (`evals/jd_extraction/recorded.json`,
unchanged) against a fixed grader and two known-wrong fixtures, at zero
capture cost. Result: **27/33 (82%)**, clearing the 80% DoD.

**Fixture fix (no code change):** case-07's `must_have_skills` dropped "SRE"
and case-11's dropped "iOS" -- both restated their own job title
("Senior Site Reliability Engineer", "Senior iOS Engineer") and should have
been excluded under the same title-redundancy rule already applied to
case-01/-09/-15. Left uncorrected in round 1 on purpose, per the explicit
instruction to stop iterating that round regardless of outcome.

**Grading fix (`jscc/evals.py`):** `must_have_skills` moved from exact set
equality to word-set containment (`_skill_matches`) -- an expected phrase
matches an actual one if either's normalized, singularized word set is a
subset of the other's. This forgives wording, not scope: "spreadsheets" now
matches "spreadsheet fluency", "GPU hardware" matches "GPUs", "model
deployment" matches "production model deployment" -- but an addition with no
matching expected slot still fails the case, same as before. An expected
entry can also be a list of alternatives (`["applied statistics",
"data science"]`) for a genuine closed "X or Y" requirement in the JD text
(case-13) -- naming either satisfies the slot; scoped to that one case, not
applied speculatively elsewhere.

**Explicitly NOT changed:** no blanket leniency for "extra but true"
additions beyond the literal Requirements-section text (case-06's
"ML"/"MLOps", case-26's stack-description leakage, case-30's
"ideally"-qualified items, from round 1's failure notes). Re-graded against
the fixed grader instead of assumed correct -- an addition only passes now if
it's the same skill in different words as something already expected; a true
addition genuinely outside the Requirements section still fails, since
`must_have_skills`'s contract is "explicit requirements," not "anything
true."

**Still failing at 82% (6 cases) -- unchanged categories from round 1, not
new ones:** level instability on ambiguous titles (case-19, case-21) and
inconsistent extraction of legitimate non-title-redundant competency phrases
across near-identical text (case-14, case-29, case-31, case-33) --
model-consistency questions the grading fix doesn't touch, held for a
separate decision on whether they're worth another prompt pass or are
documented model-behavior limits.

+2 tests (`test_skills_containment_forgives_wording_not_scope`,
`test_skills_alternatives_slot_satisfied_by_either_option`); 322 total.

### B2b capture round 1 - 64%, below the 80% bar, DoD not met

First manual-capture round against real Haiku output (no Console account
exists for this project -- see decisions-log 2026-09-11 -- so validation runs
by hand through Claude.ai chat, not a live key). Three rounds of prompt
iteration against all 33 cases: 36% -> 64% -> 64%. The DoD (`eval
jd_extraction --replay` >= 80%) is **not met**. Landing the honest number and
the harness fixes rather than continuing to chase it -- further iteration is
parked, not abandoned.

**What actually moved the number, in order of impact:**
- The original 15-case suite's `must_have_skills` fixtures were themselves
  wrong in ~16 places -- missing literal Requirements-section terms the
  fixture author (not the model) failed to include. Correcting those, not
  prompt changes, closed most of the gap from 36% to the mid-60s.
- Prompt guidance for level-mapping on manager/founder titles, single-figure
  and hourly comp, country-vs-city location, and excluding Preferred-section
  items each fixed their targeted case.
- `_normalize` in `jscc/evals.py` now folds hyphens to spaces before
  comparison (`infrastructure-as-code` == `infrastructure as code`) --
  harness fix, not a prompt one.

**What's still open, categorized rather than left as an unexplained number:**
- **Level instability on inherently ambiguous titles** (case-19 "Founding
  Backend Engineer": senior -> staff -> principal across three rounds;
  case-21, a title with no seniority word: senior -> mid -> senior). Neither
  prompt wording fully pinned these down across repeated runs against the
  same text -- flagged as a real model-consistency limit, not a wording gap.
- **Inconsistent inclusion of legitimate, non-title-redundant competency
  phrases** ("managing managers", "backend systems", "infrastructure
  engineering") -- present in some rounds, dropped in others, on
  near-identically-phrased requirements in the same batch (case-06 kept both
  phrases this round; case-33's near-twin phrasing dropped both). Genuine
  model variance holding the prompt constant.
- **Tokenization-granularity brittleness** on compound technical phrases
  ("rack-scale GPU hardware" vs "GPU hardware" vs "GPUs") and on closed
  "X or Y" requirements where the model names only one of two equally-valid
  alternatives (case-13). Exact-set-equality grading is brittle to this by
  design (the eval strategy doc accepts "Postgres" vs "PostgreSQL" as a real
  difference); a fuzzy/semantic grader would resolve it but is out of scope
  for this hand-rolled harness.
- **Two known fixture inconsistencies not yet corrected**: case-07's
  `must_have_skills` still includes "SRE" and case-11's still includes "iOS"
  -- both restate their own job title ("Site Reliability Engineer", "iOS
  Engineer") and should have been dropped under the same title-redundancy
  rule applied everywhere else (case-01, -09, -15). Left as a punch-list item
  rather than fixed now, per the explicit instruction to stop iterating this
  round regardless of outcome.

Also fixed in passing: two `long`-group cases had literal fake email
addresses in their raw JD text, which the pre-commit scanner correctly
flagged. Rewritten to describe the contact ("our recruiting team") without an
email-shaped string -- same fix pattern as prior scanner false-positive
rounds, describe the shape, don't requote the pattern.

- 0 new tests (grading/fixture/prompt changes only); 320 still passing.

### B2b prep - eval suite sized up, `company` folded into extraction

No Anthropic Console account exists for this project, so B2b closes by hand
-- each eval prompt run once through Claude.ai chat, the completion captured
and replayed -- rather than against a live key. That made two gaps in the
eval suite itself worth fixing before the first capture round, since a
hand-capture round is expensive to redo.

**15 cases wasn't enough to make the 80% threshold mean anything.** Each case
was worth ~6.7 points; the standard error on the pass rate at that size was
about ±10 points, so a perfectly stable prompt could show anywhere from 70%
to 90% depending on which 15 cases happened to be picked. Scaled to 25 short
cases (~±8pt SE) plus 8 new `long`-group cases at realistic fetched-page
length and noise (nav breadcrumb, EEO/benefits boilerplate) -- the original
15 were all clean and averaged 357 characters, an order of magnitude short
of what the B3b smoke test found on real postings. The long cases are
hand-authored from the shape of three real postings, not copied from them:
`cases.json` is tracked and public, so verbatim third-party posting text
would be a copyright problem, the same reason `docs/smoke-test-results.md`
never stored raw fetched text either. `format_eval_summary` now reports
`short`/`long` pass rates as a breakdown; the combined figure still alone
gates `--min-pass-rate`, unchanged from B7's exit contract.

**`ExtractedJD` had no `company` field**, so `ingest`/`resolve-dlq` always
fell back to a URL-domain guess (`_company_from_url`) even when the JD text
named the employer outright -- a known gap since B3a. Folded in now because
it touches the same three files (prompt, `cases.json`, eval suite) the
sizing pass already had open. `company` is nullable and graded like `title`
(normalized match) since some postings never name the employer. Company
resolution in `cli.py` now has an explicit precedence: an explicit
`--company` (the user's deliberate override) beats extraction, which beats
the URL-domain fallback -- previously `--company` and the fallback were
already folded together before extraction had an opinion to override.

Full rationale, including the standard-error math: decisions-log 2026-09-11
and jscc.md's B2b section.

- 33 eval cases (was 15). 320 tests, no net change in count (three existing
  test payloads gained a `company` key).

### B14 - the README's test count, kept honest by the suite it counts

The README said 304 pytest cases. The suite had 317. Four slices had added
tests without touching the number, which is the fourth time this figure has
gone stale.

A small lie, but badly placed: it sits two lines from the claim that the sample
output reproduces exactly, in the one document a cold reader trusts most, in a
repo whose whole pitch is that the engineering is honest. Anyone who runs the
suite sees the mismatch immediately.

Fixed the number, then made it unable to drift again. One test compares the
figure the README states against what the run actually collected; a second
checks the two places the count appears still agree, because fixing one and
leaving the other is the obvious near miss.

The fiddly half is the guard. `-k`, `-m`, `--lf`, and naming specific files all
collect a subset by design, and asserting against those would fail for reasons
that have nothing to do with the README, so the test skips there and holds on
the full run -- which is what CI does. Verified in all three modes, and by
setting the count back to 304 and watching both tests fail.

- 2 tests (319 total).


### B13 - the scanner learns what an API key looks like

Nothing in either egress point would have stopped an Anthropic API key. The
scanner matches email and phone shapes and danger-list literals; a key matches
none of them. `.env` is gitignored, so the obvious path was covered, but a key
pasted into a note, a fixture, or a config file committed cleanly. Worth fixing
before a real key exists on the machine rather than after.

`CREDENTIAL_RE` lives in `personal_data.py`, so one edit reached both egress
points -- which is the property that module exists to have, and the scanner test
asserts the git half actually arrived rather than assuming it.

**A credential is not personal data, and it is not filed as though it were.**
It carries its own reason label and its own token, so D8's claim -- which is
about personal *identity* -- neither widens nor blurs. What it shares with the
rest of the module is the boundary: this is what must not cross an egress point.
Reusing the "personal" label to save a few lines would have traded a precise
safety claim for plumbing, which is the trade D7 and D8 exist to refuse.

**Redaction order turned out to be load-bearing again.** A key body is
alphanumeric with dashes, so a digit run inside one sits squarely in the phone
rule's window. With phones running first, the run becomes a phone token and
leaves a string that is still most of a key and no longer matches
`CREDENTIAL_RE` -- a partial redaction that reads exactly like a complete one.
Credentials now run first, ahead of emails, for the same reason emails already
ran ahead of phones. Verified by reordering and watching the test fail.

The two halves behave differently on purpose: the scanner **blocks**, the
sanitizer **redacts**. Blocking is the half that matters for a key, because a
committed key is the damage; the sanitizer's contract is to rewrite
unconditionally and never refuse work it can make safe.

Deliberately narrow: the `sk-ant-` prefix plus a long body, no attempt at
"any high-entropy string" -- that would fire on hashes, UUIDs and base64 until
someone switched it off, and a rule people switch off protects nothing. It does
not cover other vendors' formats, which is a stated limit rather than an
oversight: this repo talks to one API, and a list of half-remembered prefixes
would read as broader coverage than it has.

Key-shaped fixtures are assembled at runtime rather than written as literals.
Both test files are on the exclude list today, but a fixture that depends on
staying excluded breaks the day the list is tidied -- and these tests exist
precisely because a key-shaped string should not survive a commit.

- 6 tests (317 total), verified by deletion in both halves: reordering the
  substitution, and dropping the rule from the detection path.


### B12 - correct the Haiku rates before any real spend

The ledger priced extraction at $0.80 / $4.00 per MTok. The published rates are
$1.00 / $5.00, so every recorded cost was 20% under the truth.

This is the failure mode `rates_for` was built to prevent, arriving by the one
route that guard cannot see. Refusing an unknown model stops cost being invented
when something is *missing*; a wrong rate for a model that is present passes
every check, because nothing is missing. The comment above the table already
said to verify before trusting the figures -- which is a note to a reader, not a
mechanism.

Rates verified 2026-09-05 against the published pricing page, with the date and
the source recorded beside them so the next check has somewhere to start.

Found while working out how to cap spend on a real key. It matters more there
than in the ledger: a budget sized from these numbers would have been set 20%
low against real billing, so the cap would bite before the spend it was sized
for.


### B11 — the synthetic fixture stops being a tracked file (W9)

`eval jd_extraction` wrote ledger rows into `data/synthetic.db`, a checked-in
file, so the demo command dirtied the repo. During B2b, which is dozens of eval
runs, that would have been every iteration. Two things underneath it turned out
to be worse than the symptom.

**`reset_tables` listed four of the six tables.** It hardcoded `interactions`,
`dlq_entries`, `contacts` and `applications`, and silently skipped `llm_calls`.
So ledger rows survived a reseed, and the "deterministic fixture" was
deterministic only in the tables someone had remembered to add to a list. The
list is now read from `sqlite_master`, with `meta` — the mode stamp whose
survival is the D7 M1 guarantee — exempt by name. A list kept in sync with the
schema by hand drifts from it; asking the database what it contains cannot.

**A reseed does not restore the file anyway.** Verified: seed, run an eval,
reseed with identical arguments, and the bytes differ — SQLite page allocation
does not reproduce for identical rows. So the tracked fixture could not be
returned to its committed state by *any* command. "Reseed before committing"
was never available, and the file was permanently dirty after any write.

**So it is no longer tracked.** It was already vestigial: the quick start runs
`db init`, `seed`, `report`, which overwrites the fixture at step three, so
nothing as shipped ever read the committed bytes. The `.gitignore` negation
`!data/synthetic.db` is gone with it, which removes the one carve-out in the
D7 M2 rules that had to be got right. The safety substance of M1 is two
instances and a stamped marker, not which one is in git — that is unchanged.
This also closes the wider version of the same finding, which W9 only named for
evals: `ingest` in the default synthetic mode dirtied it identically, and that
is the primary workflow.

**The test that was supposed to prove the fixture was scrubbed could not fail.**
`test_synthetic_fixture_passes_scanner` ran the pre-commit scanner over the
committed `.db`. `scan_file` reads UTF-8 and returns no hits on
`UnicodeDecodeError` — a whole-file skip, which a SQLite header triggers
immediately. It asserted a return code that could not have been anything but
zero. Its docstring described a per-region skip the scanner does not implement,
and it was cited as closing two findings from the A10 review. Same class as
L-1: an assertion whose subject cannot produce the failure it checks for.

Replaced by three tests. One dumps the fixture's text columns and scans *that*,
across three seeds, so the claim is about the generator's name pool rather than
about one frozen output. One pins the binary-skip limit explicitly, with the
same string caught in a text file to prove the miss is the skip and not a hole
in the email pattern. One re-runs the fixture scan with a single address
appended, so if the real assertion ever goes vacuous again it says so instead of
staying green. Verified by poisoning `notes` with an email and watching the old
test pass and the new one fail.

Scoping the dump to non-identifier columns was necessary and is a rule, not a
convenience: `uuid4()` primary keys contain digit runs that trip the phone
pattern, which is the sixth appearance of that false-positive class. The
pattern stayed untouched, as it always does.

D7 M3 now records the binary limit: a green scan over a `.db` means the file
was skipped, and databases are protected by M1/M2 keeping them untracked, never
by M3 having looked.

- 2 tests net (311 total), verified by deletion in both directions — poisoning
  the fixture, and reverting the reset fix.


### B10 — the prose pass

The walkthrough lens found that almost all the remaining damage was prose: the
repo described a system slightly better than the one that exists. Five items,
none of them subtle once a cold reader hits them.

**The sample output did not reproduce, directly under the words
"Bit-reproducible."** Funnel counts depend only on stored state; the stale block
is measured against a reference instant, and `report` had no `--now`, so it read
the wall clock and drifted a day per day. The reviewer ran the printed quick
start and got different numbers — the likeliest first action anyone takes.

Fixed by making the claim true rather than by qualifying it: `report` now takes
`--now`, sharing one parser with `seed` so the two halves of the quick start
cannot disagree about the format. With both pinned, the published block
reproduces verbatim, and the README now prints all thirteen rows so it can be
diffed rather than trusted. Three tests pin the exact published figures, assert
the unpinned path still drifts, and check the shared parser rejects a naive
timestamp from either command.

**Present-tense claims for a drafter and a scorer that do not exist.** The
opening line said the tool "scores fit" and "drafts follow-ups"; signal #3 of
three described the drafter's routing behaviour in the present tense. Both are
design, not system. Restated as the locked principles they are, and `Status`
now has an explicit **Not built** paragraph. The reviewer's read is the right
one: stating a principle with the code unbuilt reads *more* senior, not less.

**README status contradicted itself in the first screenful.** L5 and L7 said
Phase B was *next* while L113 listed five shipped Phase B slices. This had been
recorded once already, at a different line, and fixed there only — a finding can
be closed at the location it was reported and still be live everywhere else.

**Sanitizer docstrings still described Phase A.** The file a reviewer opens to
check the headline safety claim opened *"Phase A6 hardened skeleton"* and said
*"Phase B will replace the return with a real LLM call."* Phase B shipped, and
it did not do that: `send_to_llm` stayed a verification gate and the network
call went to `llm_client`. The docstring now says what shipped and why the
split is the point — and names the honest limit, that the client underneath
still takes bare strings, so the contract rests on `send_to_llm` being the only
route to it.

**D7 advertised "seven concrete mitigations"; two were not built.** M6's
persistent "SYNTHETIC MODE" banner has no UI, and "LLM budget caps enforced via
the A5 instrumentation" has no cap — metering is the prerequisite for a cap,
not a cap. Both marked `(planned)`, with what *is* built stated alongside. Five
of seven verifiable is a good number; seven claimed and five real is not.

**Also:** `scan_tracked.sh` said the scanner blocks "name/email/phone patterns"
— there is no name pattern, names are danger-list literals. `evals/README.md`
opened "Three suites" and self-corrected eighteen lines later. The `eval`
command's docstring still said it exits non-zero "if any case fails", which B7
had changed to a pass *rate*.

**One conflict, found and closed here.** B7 recorded that review-finding IDs
were out of production code. One was left: `send_to_llm` still cited
*"walkthrough finding #2 from the A10 gate."* The claim was in the CHANGELOG and
the counter-example was in the file the claim was about.

- 3 tests (307 total), verified by deleting the `now=` argument and watching
  both the pinned and the drift assertions fail.


### B9 — decode responses correctly (M-2)

`_read_body` decoded with `response.encoding or "utf-8"`. That fallback was
dead code: `requests` fills `encoding` in with **ISO-8859-1** for any `text/*`
response carrying no charset parameter, following an HTTP/1.1 default that RFC
7231 removed. At the attribute, "the server said Latin-1" and "the server said
nothing" are indistinguishable — so every undeclared UTF-8 page, which is to say
most of the modern web, came through as mojibake. The docstring claimed "the
declared charset, else UTF-8", which described a code path that could not run.

The corrupted text is both what goes to the model and what is stored as
`source_raw`. Accented names, em-dashes and smart quotes are ubiquitous in job
postings, so during B2b this would have degraded extraction quality while
looking exactly like a bad prompt.

Decoding now follows the order the HTML standard actually specifies: the charset
the server declared (parsed from the raw header, so "declared nothing" stays
distinguishable), then a `<meta charset>` in the first 4KB, then UTF-8 **strict**
so failure is detectable rather than silently mangled, then cp1252 with
replacement as a last resort rather than a first assumption.

**A second defect fell out of the same line.** An unrecognised charset name
raised `LookupError` straight out of `fetch_jd` — an uncaught exception from the
module whose contract is that it never raises for content-shaped failures, the
same class as H1. An unknown name now falls through to the next strategy.

**Known limit, written down rather than guessed at:** a legacy page in a
non-Latin encoding that declares nothing anywhere still decodes wrong.
Statistical detection would cover it, at the cost of a direct dependency on a
charset-detection library. Not worth it for job postings.

**The test fake was hiding the bug.** The first version of these tests passed
against the *old* decoder, because `_mock_response` hard-coded
`encoding="utf-8"` — kinder than the library, and precisely the property under
test. The fake now derives `.encoding` through
`requests.utils.get_encoding_from_headers`, exactly as requests does, with an
assertion in the helper that it really does yield Latin-1 for a bare
`text/html`. Same lesson as H-4, one layer over: a fake that is more reasonable
than the real thing makes the bug invisible to the test written to catch it.

- 6 tests (304 total), verified by reverting the decoder and watching the
  headline case fail.


### B8 — regression protection for the two guards that had none (H-4, M-4)

Both fixes worked. Neither was defended: delete the guard and the suite stayed
green, which is the state C1 was in for three slices.

**H-4 — the M5 fetch guards were invisible to their own tests.** Every guard
test patched `requests.get` wholesale, so the guard itself — the
`allow_redirects=False, stream=True` arguments — was never observed. Flipping
`allow_redirects` to `True` left all 30 fetcher tests passing, including
`test_redirect_into_a_private_address_is_refused`. With redirects followed
internally, requests resolves the second hop itself: `_check_url` never sees
that URL, and `fetch_jd` receives the final response as though it had been the
first. The test that named the property could not observe the property.

Two tests now cover it. One asserts the arguments. The better one drops a layer
lower, patching `HTTPAdapter.send`, and asserts that **exactly one request
leaves the process** for a 302 into a private address, and that both hostnames
reached the resolver. That is the behaviour rather than the implementation, and
it is false the moment the guard is removed.

A detail worth recording: requests calls `resolve_redirects` even when
`allow_redirects=False`, once, with `yield_requests=True` to populate
`Response.next`. That prepares a request without sending it — so "one send" is
the correct expectation and not an artifact of the fake.

**M-4 — nothing proved redaction reached the client.** `test_sanitizer.py`
proves `sanitize_for_llm` redacts; no test proved the redacted value is what
`extract_jd` actually hands over. Rewiring the call to pass `raw_text` instead
of `verified["user"]` left the whole suite green — and C1 was exactly "the
sanitizer was a no-op and nobody noticed for three slices." Three tests now
drive a spy client with a JD carrying an email and a phone: both branches of
`extract_jd` (with and without a ledger connection) must hand over the redacted
text, and the caller's own string must come back untouched, since D7 governs
egress and not the user's local records.

**Each of these was verified by deletion, not by passing.** Flip
`allow_redirects`, flip `stream`, or rewire the payload, and the specific tests
fail. A test that cannot fail when the thing it covers is removed is not
covering it — which is the whole finding, and was worth proving rather than
asserting.

- 5 tests (298 total).


### B7 — three decisions, and a narration trim

**Exit codes are now a contract.** `ingest` and `resolve-dlq` return 0 when a
record was created, **3** when a handled failure wrote a DLQ entry, 2 for a
usage or configuration error, 1 for anything unexpected. D6 treats a queued
failure as an expected product state, so a script looping over URLs has to be
able to tell "this is waiting for you" from "the tool broke" — folding both
into 1 erases the distinction the queue exists to make, and leaving the queued
case at 0 claims an Application that does not exist. The check commands
(`validate-config`, `eval`) keep the conventional 0/1: "did the check pass" and
"what happened to the work" are different questions and one scale answers both
badly. This also removed an inconsistency where the same parse failure exited 0
from `ingest` and 1 from `resolve-dlq`.

**The eval suite has a threshold, and can run without a key.** `PASS_THRESHOLD
= 0.80` lives in `evals.py` with a `--min-pass-rate` override, and the command
fails below the *rate* rather than on any single failing case — which is what
the README always claimed. `--record` captures live responses to
`evals/jd_extraction/recorded.json`; `--replay` serves them with no key, no
spend and no network.

Recordings are keyed by a hash of the prompt the *client* receives, i.e. after
sanitization. Keying on the case id would let a recording keep replaying after
the prompt or the redaction rules moved underneath it, which is how a recorded
suite starts lying. Be precise about what replay buys: it pins the harness, the
parser and the prompt's output contract; it does not measure the model's
judgment. Only a live run does that. CI wiring waits for B2b, since replaying
stub responses would gate on 0/15.

**Narration trimmed.** Production code no longer carries review-finding IDs or
the remediation history behind each fix. The *rules* stay, because they are what
a reader needs ("nothing that can fail after the money is spent belongs inside
an instrumented function"); the bookkeeping lives here, where an audit trail
belongs. Code that narrates its own remediation history reads as over-produced,
and the ID is meaningless to anyone without the gate doc open.

Also renamed `test_ingest_never_crashes_on_fetch_exception_shaped_failure` to
`test_ingest_converts_a_fetchresult_failure_to_a_dlq_entry` and gave it the DLQ
assertions its docstring always described. **The B5 entry below claimed this
rename had happened; it had not** — only the docstring had changed.

- 8 tests (293 total).

### B6 — rerun-gate fixes (H-1, M-3, H-2, H-3, and one follow-on)

A second two-lens gate ran at `30c2a1e`, with both reviewers reading **cold** —
neither saw the previous gate's findings until it had formed its own, then each
labelled every finding NEW / REPEAT-OF / CONTRADICTS. That change immediately
produced a CONTRADICTS against a fix from the slice under review, and both
lenses independently found the same hole.

**H-1 — the danger list resolved against the process working directory.**
`Path(".safety/danger-list.txt")` is relative, so `default_danger_terms()`
returned `[]` for any process not started from the repo root. Silently: email
and phone redaction kept working, so nothing looked broken, while the user's
list of real names — the half of D8's guarantee the regexes explicitly cannot
do — was simply absent.

This is the load-bearing half of the C1 fix. The pre-commit scanner always runs
from the repo root; the sanitizer runs wherever the user happens to be. **So the
two D7 egress points drifted on what counts as personal after all** — not
through the duplicated regexes C1 removed, but through path resolution. C1's
argument was "structural, not disciplinary," and a control whose effectiveness
depends on remembering to `cd` first is disciplinary.

**A follow-on found while fixing it:** `precommit_scan.py` read only the
*tracked* scaffold, never `.safety/danger-list.local.txt` — the file a user
actually edits. A term added there blocked LLM egress and not commits, the
reverse of C1's stated "one edit blocks both." Sharing the regexes was never
sufficient on its own: the two enforcement points also have to agree on which
files define the terms and where those files live.

**M-3 — same root cause, one layer out.** `DEFAULT_DATA_DIR = Path("data")`
meant `JSCC_DATA=real` from any other directory created a fresh,
correctly-stamped `real.db` **outside the `.gitignore` that is D7 M2** — exit 0,
success message, no warning. The mode marker did its job; the file just wasn't
where the protections are. One wrong `cd` disabled two of D7's seven mitigations
at once.

The fix is one definition of where things live: `jscc/paths.py` holds
`PACKAGE_ROOT` and `mode.py`, `cli.py`, `personal_data.py` and `evals.py` all
anchor to it. Three modules declaring their own copy would be the same
duplication that caused the bug — `evals.py` had already got this right alone,
which is exactly how the inconsistency hid. Explicit overrides (`--data-dir`,
`--config-dir`, `JSCC_SAFETY_DIR`) are untouched: a user saying where to look is
different from a default that quietly depends on where they were standing.
`JSCC_SAFETY_DIR` pointing nowhere raises, and a missing default directory warns
— the silent-empty-list behaviour was the bug.

**H-2 — an extraction failure crashed `ingest` with a raw traceback and no DLQ
entry.** `ExtractionParseError` had no handler between the CLI helper and
`main()`. A model that wraps its JSON in prose or a ``` fence — the *normal*
behaviour — produced exit 1, no Application, no DLQ row, nothing to retry from.
B3a's DoD is "produces Application OR DLQEntry, never crashes"; H1 restored that
for the fetch stage only, and this is the same bug class one layer up, in the
layer that goes live the moment B2b sets a key.

Both paths now route a parse failure to `extraction_failed`. A pasted JD has no
URL and `DLQEntry.source_url` is NOT NULL, so those entries carry a `(pasted)`
sentinel that `resolve-dlq` recognises rather than inferring a company from it.
A failed `resolve-dlq` creates no second entry — the existing one stays
unresolved, which is already the correct record.

**Truncation is now distinguishable from bad JSON.** `AnthropicClient` passes
`max_tokens=1024` and never looked at `stop_reason`, so a long JD produced
incomplete JSON and a `JSONDecodeError` identical to the one a badly-worded
prompt gives. `LLMResponse` carries `stop_reason` and `extract_jd` raises naming
truncation and the output-token count — checked *after* the ledger row is
written, since doing it inside the instrumented call is the M2 mistake.

**Deliberately not DLQ'd:** an `UnknownModelPricingError` exits 2 with a
configuration message. Every ingest would fail identically on a misconfigured
model, so queueing entries that re-fail on resolve would bury the one message
worth reading — and nothing was billed, since M4's price check runs first.
`SanitizerRefusal` and `LLMSendError` still propagate uncaught, per
`sanitizer.py`'s instruction that callers must not catch and continue.

**H-3 — the extraction result was thrown away.** The helper used
`extracted.title` and nothing else; the other six fields were computed, billed,
instrumented and dropped, and `Application.extracted_jd` was `None` on every row
production wrote. D9's second justification for the split-call architecture is
that *"intermediate output has independent product value"* — the column, the
model field, the JSON serializer and the seed fixture all existed to hold data
nothing ever stored. One line to fix; a coverage test now pins the stored keys
to `ExtractedJD.model_fields` so a field added later cannot be silently dropped.

- 18 tests (285 total). The path tests `chdir` for real rather than patching the
  path, because the bug *was* the real resolution depending on the real working
  directory — a test that mocks the path away cannot see it.

### B5 — Phase B gate hardening (C1, H1, H2, M1–M5)

From the Phase B → C two-lens gate, run ahead of B2b's live prompt iteration.

**C1 — the sanitizer authenticated payloads it never redacted.** `_transform`
had been an identity snapshot since A6, deferring real redaction to "Phase B,
when the first prompt is written." Phase B then shipped a prompt, two production
call sites and an arbitrary-pasted-text path without it, while D7 M5 claimed the
sanitizer "redacts contact names to role tokens" and D8 claimed the tool
"structurally cannot send identifiable person information to a third-party LLM."
Demonstrated concretely: a string carrying a name, email and phone was *blocked
from git* by the M3 scanner and *forwarded verbatim to the LLM* by the M5
sanitizer — two mitigations named in the same principle, disagreeing completely
about what counts as personal data.

The fix is one definition, two enforcement points. `jscc/personal_data.py` holds
the patterns, the digit-count filter, danger-list loading, a `find_personal()`
detection half and a `redact()` rewrite half; `precommit_scan.py` imports its
rules from there instead of holding a second copy. `_transform` snapshots as
before (the deep-copy that closes the earlier TOCTOU is unchanged), then redacts
every string **before** the authenticator is computed, so the HMAC covers the
redacted bytes and no verified path can carry the original text.

Redaction is **unconditional** — it does not consult `contains_personal` and no
caller can opt out. `extract_jd` hardcodes that flag `False`, so anything
depending on it was disciplinary, not structural. `model` is the one control key
excluded: model ids carry a date-shaped digit run the phone heuristic matches,
and rewriting it would break the call.

**Scope, stated honestly.** This removes structured identifiers, danger-list
literals and supplied contact names. It does *not* do free-text NER — an
unfamiliar name in pasted prose, with nothing else to key on, is not detected.
D8 says so explicitly rather than implying a broader guarantee; an overstated
safety claim is worse than a narrow one. Local storage is deliberately
untouched: D7 governs egress, not the user's own records.

**H1 — `fetch_jd` crashed on an empty response body.** `lxml.html.fromstring("")`
raises `ParserError`, and an empty-body `200` is a routine bot-block response.
That exception escaped and terminated `ingest` with exit 1 and no DLQ entry.
`_extract` now degrades to empty text so the body flows down the existing
thin-content path. The catch is deliberately broad; narrowing it to today's
exception types would reinvite the bug the next time lxml raises something new.

**H2 — the eval suite didn't grade `title` or `location`.** Both are specified
by all 15 cases, and the harness read those expectations and dropped them
because neither field appeared in any graded-field tuple. `title` is the only
extracted field with a production consumer. Each field now has the comparison
rule its content warrants: normalized match for `title`; presence plus
containment for `location`, so a remote role yielding null still fails but
`"Denver"` vs `"Denver, CO"` passes while `"Denver"` vs `"Seattle"` fails;
normalized set equality for skills. A `_GRADED_FIELDS` tuple plus a coverage
test closes the class, not just the two instances.

**M1 — eval runs bypassed instrumentation.** `eval jd_extraction` now takes
`--data-dir` and meters under a separate `extraction_eval` feature, so iteration
spend shows up in `jscc costs` without inflating the per-application figure.

**M2 — a billed call that failed to parse was never recorded.** `_parse_response`
ran inside the instrumented function, so the exception beat the ledger write:
tokens spent, nothing recorded. Malformed output is the likeliest failure while
iterating on a prompt — exactly when the cost figures are being read — so
`jscc costs` under-reported the runs that went wrong. Parsing moved out.

**M3 — the eval harness graded safety failures as prompt failures.** A
`SanitizerRefusal` across all 15 cases reported as a routine `0/15 passed`. Both
it and `LLMSendError` now propagate and abort the run.

**M4 — unknown models were priced at the Haiku rate**, so a swap to Sonnet or
Opus would under-report spend by roughly an order of magnitude, silently.
`rates_for()` raises, and the check runs *before* the request is sent — raising
afterwards would spend the tokens and discard the record, which is M2 again.

**M5 — the fetcher was an unguarded server-side request primitive.** Any string
reached `requests.get`, redirects followed, no size cap, and whatever came back
was forwarded to a third-party model. Requests now leave through `_get_guarded`:
an http(s) scheme allowlist, rejection of hosts resolving to non-public
addresses (checked against the *resolved* addresses, since a hostname with an A
record pointing inside is the same attack as the literal), the same check re-run
on every redirect hop, a redirect ceiling, and a streamed 5 MB cap enforced
mid-stream rather than from `Content-Length`, which is optional and can lie.

Practical risk was low — the user types the URL — and the gate said so. Fixed
anyway, because "the usage pattern makes it unlikely" is the disciplinary
argument D7 exists to reject.

- 64 tests (267 total).

### B4 — JD paste-only path

Per D6, the escape hatch for a site the fetcher can't crack at all: no URL, no
fetch attempt, no DLQ detour.

- `ingest --paste` reads JD text from stdin, `--file <path>` from a file. Both
  produce an `Application` through the same helper `resolve-dlq --paste-text`
  uses, so "same shape as the URL path" is structural, not conventional.
- `--url` and `--paste`/`--file` are mutually exclusive; neither is a
  `UsageError`. Empty input exits non-zero rather than creating a blank record.
- No URL means no domain to derive a company from, so `--company` was added,
  defaulting to `"(pasted)"`.
- 5 tests (203 total).

### B3b — Playwright fallback + real-URL smoke test

- `config/pipeline.yaml`, `playwright_fallback: false` by default — a ~200MB
  browser binary stays off unless asked for. JS-required detection reuses the
  thin-content heuristic: "site refused to render server-side" and "genuine SPA
  shell" are indistinguishable without rendering, so both route the same way.
- `scripts/smoke_fetch.py`: real-URL smoke test, not CI-gated, results
  snapshotted to `docs/smoke-test-results.md`. Against 5 live postings chosen
  via browser rather than guessed, an SPA career site and an authwalled jobs
  search both went `extraction_failed` with the flag off and `ok` with it on; a
  straight 403 stayed `blocked` either way, correctly — a server-side bot-block
  is not something the fallback was meant to route around.
- New opt-in dep: `playwright` (confirmed explicitly, per the no-new-deps rule).
- 9 tests (198 total).

### B3a — baseline fetcher + DLQ core

- `fetch_jd(url)`: `requests` + `readability-lxml`, classifying every non-2xx or
  exception into a `FailureMode` (402 → paywall; 401/403/429/451/5xx → blocked;
  timeout; under-200-chars → extraction_failed). Never raises for network- or
  content-shaped failures.
- `ingest --url`, `dlq list [--all]`, `resolve-dlq <id> --paste-text` — the D6
  escape hatch, and the code path B4 later reused.
- New deps: `requests`, `readability-lxml` (over `trafilatura` — lighter, and it
  matches the sub-plan's wording).
- **Known gap, logged not fixed:** `ExtractedJD` has no `company` field, so a
  placeholder is derived from the URL domain.
- 16 tests (189 total).

### B2 — JD extraction prompt v1 + LLM client plumbing

No `ANTHROPIC_API_KEY` in this environment, so this landed the real prompt and
the full call path behind a `StubExtractionClient`, exercisable end-to-end with
a real key as a drop-in swap. Scoped honestly as prompt-authored-and-wired, not
prompt-validated: the ≥80% DoD needs live iteration (B2b).

- `llm_client.py`: `LLMClient` protocol, `AnthropicClient` (raises immediately
  without a key — never silently degrades), `StubExtractionClient`,
  `default_client()` choosing once, visibly.
- `extraction.py`: `EXTRACTION_SYSTEM_PROMPT` targeting all 7 fields. Every call
  routes through `sanitize_for_llm` → `send_to_llm` before any client call.
- `EXTRACTION_MODEL` is built by string concatenation: the contiguous digit run
  in the model id trips the phone-pattern scanner. **This false positive has now
  recurred five times** (danger-list example, model id, a comment quoting the
  model id, lockfile hashes, IP literals in tests). The convention is fixed:
  split the literal, or describe the pattern's shape in prose — never loosen the
  regex, which has never once blocked real content.
- 12 tests (173 total).

### B1 — JD extraction eval suite

Per D9, extraction and scoring are split so extraction facts can be graded
independently of scoring judgment; this suite is that independence made concrete.

- `ExtractedJD` — the contract B2's prompt is written against.
- `evals/jd_extraction/cases.json`: 15 hand-authored JDs (fictional companies)
  across levels junior→director, with and without stated comp, across
  remote/hybrid/onsite.
- Hand-rolled harness: structural fields compared exactly, skills by set
  equality, `comp_band` by presence (exact dollar figures are too brittle to pin
  a prompt to), prose checked non-empty only — LLM-judge grading waits until
  there is a prompt worth judging.
- 12 tests (161 total). DoD met: the harness runs and reports `0/15` — it works,
  there is just no prompt yet.

### A5 — LLM call instrumentation (deferred from Phase A, landed at Phase B start)

Per D5 this was meant to be Phase A foundation so no LLM call could ever go
uninstrumented. It slipped out of the A1–A4.5 sequence and **no gate round
caught it** — all three rounds reviewed *shipped* code, and nothing had reason to
look for a missing slice. Found while reading the sub-plan back before Phase B.

- `@instrumented(feature)` captures call_id, feature, model, `sha256(prompt)`
  (never the prompt — the D8 boundary applies here too), tokens, cost, latency,
  timestamp. `llm_calls` table (schema v3), `jscc costs` CLI.
- 7 tests (149 total).

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

**Still open from Phase A** (untouched, unblocked):

- Walkthrough #5 — ADR-001 rewrite or delete; a real framing judgment call.
- Walkthrough #6 — coverage badge; needs `pytest-cov` and a workflow step.
- Walkthrough #7 — CHANGELOG split; partly addressed by this compaction.
- L-json-default-sanitizer-1 — strict `_stable_json`; real once payloads have
  richer types.
- L-report-format-injection-1 — control-char escaping in `format_report`.
