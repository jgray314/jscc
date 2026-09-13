# Evals

One suite per LLM stage. Two stages exist today — `jd_extraction` and `fit_scoring` (D9 splits extraction from scoring so facts and judgment regress independently). Routing/composition arrives in Phase D (D10). Each suite is a JSON case file plus a grading function in `jscc/evals.py`.

## jd_extraction (Slice B1)

`evals/jd_extraction/cases.json` — 33 hand-authored JDs (fictional companies, no real personal data) with expected `ExtractedJD` fields, split into two `group`s:
- **`short`** (25 cases) — clean, paste-shaped JD text averaging ~360 characters, matching what a human pastes via `ingest --paste`. Covers the level/comp/location/remote/skills cross-product plus edge cases (ambiguous comp, multiple locations, no named skills, terse input).
- **`long`** (8 cases) — synthetic-but-realistic fetched-page length and noise (nav breadcrumb, benefits list, EEO/export-control boilerplate, application form fields) at roughly 2,000-3,000 characters, matching the order-of-magnitude the B3b smoke test found on real postings. Written from scratch, not copied from any real posting — `cases.json` is a tracked, public file, so committing verbatim third-party job-posting text would be a copyright problem, the same reason `docs/smoke-test-results.md` records only URL/outcome metadata and never raw fetched text.

The `short`/`long` split exists because 15 cases gave the pass-rate threshold a standard error of roughly ±10 points — noisy enough that a stable prompt could show anywhere from ~70% to ~90% depending on which cases happened to be in the set. 33 cases brings that to roughly ±7-8 points; see decisions-log 2026-09-11 for the full statistical rationale, including the honest caveat that this is "less noisy," not statistically airtight — a textbook-tight interval would need 60-100+ cases, impractical under [jscc.md's manual-capture plan](../context-directory/projects/ai-portfolio/jscc.md) (B2b is captured by hand through Claude.ai chat, not live API calls).

Grading (`grade_extraction` in `jscc/evals.py`):
- **Exact match:** `level`, `remote_policy`.
- **Normalized match (case/whitespace only):** `title`, `company` (nullable — some postings never name the employer; both-null passes like any other normalized-field match).
- **Set equality (order-independent):** `must_have_skills`.
- **Presence-only:** `comp_band` — both-None or both-not-None; exact dollar figures aren't graded because they're too brittle to pin a prompt to.
- **Presence + containment:** `location` — both-None/both-not-None, and one must contain the other ("Denver" vs. "Denver, CO" passes; "Denver" vs. "Seattle" fails).
- **Prose, not graded here:** `responsibilities_summary` — checked for non-empty only. Real quality grading (tone, no hallucinated facts) is an LLM-judge rubric, deferred until there's a real prompt worth judging.

Run: `python -m jscc eval jd_extraction`. Exits non-zero if the combined pass rate falls below `PASS_THRESHOLD`; `format_eval_summary` also reports a `short`/`long` breakdown so a regression says which distribution broke, without a second gate.

## Adding a case

Append an object to `cases.json` with a unique `id`, `raw_jd` (never real personal/company data — synthetic or scrubbed only, per D7/D8), an optional `group` (`"short"` default, `"long"` for fetch-shaped noise), and an `expected` dict matching `ExtractedJD`'s fields. Cover both presence and absence of `comp_band` and a mix of `remote_policy` values — the grading logic branches on those. Avoid "X or Y" phrasing in a requirements section you expect graded by `must_have_skills` — the set-equality check can't credit a model for picking either disjunct, so it fails a correct answer either way.

## fit_scoring (Slices C1, C2a)

`evals/fit_scoring/cases.json` — 25 hand-authored (JD, profile) pairs spanning the fit spectrum: clear high fit, comp below/partially-below/missing, level mismatch (below and above target, including executive scope), deal-breakers present (both structurally-tagged and detectable only from raw JD text, including one buried in unrelated boilerplate), must-haves entirely missing or satisfied only via raw-text nuance, borderline hybrid cases, comp above target (not a downside), an ambiguous minimal posting, a total role mismatch, role-focus matching only one of two profile entries or neither, a thin/empty skills list that shouldn't tank an otherwise-strong match, an ambiguous "Tech Lead" title, and two cases against a second, IC-focused profile (`role_focus: staff/principal engineer`, deal-breaker on required people management) to test that grading isn't hard-coded to one profile shape. All cases share `config/profile.example.yaml`'s base profile except those two.

**Sized at 25, not the original 10 (resized 2026-09-12, before C2b spent any manual-capture effort):** at the ≥80% threshold, the binomial standard error on a pass rate is `sqrt(p(1-p)/n)`. At n=10 that's ~13 points — noisier than the exact problem `jd_extraction` hit at n=15 (~10 points, see below), which is what drove its own resize to 33 cases. Catching the same problem here before any capture spent against it — rather than discovering it mid-round the way B2b did — is the whole point of writing it down.

Grading (`grade_fit_score` in `jscc/evals.py`):
- **Band, not exact score:** each case names a `min_score`/`max_score`. A real score band is inherently fuzzy — the eval strategy doc calls this out explicitly ("high fit 75-95", "clear pass <30") rather than pretending a fit judgment has one right answer.
- **Rationale:** checked for non-empty only. Real quality grading (does it name the right factors, no hallucinated claims) is an LLM-judge rubric, deferred until there's a real prompt worth judging — same deferral `jd_extraction`'s prose field got at B1.

Run: `python -m jscc eval fit_scoring`. C2a landed the real prompt (Sonnet, per D9) and full `--record`/`--replay`/`--min-pass-rate` parity with `jd_extraction`, but no `ANTHROPIC_API_KEY` is configured for this project — same constraint B2 hit — so today it runs end-to-end against `StubScoringClient`, a fixed placeholder score of 0. That coincidentally clears a handful of deliberately-low-fit bands (score 0 is a valid "clear low fit" answer — 8/25 today), so unlike `jd_extraction`'s stub the invariant isn't "every case fails" — it's "the pass rate stays far below `PASS_THRESHOLD`," which it does (no constant score clears the bar across bands spanning 0-100). C2b is the manual-capture round (through Claude.ai chat, mirroring B2b) that actually validates the prompt.

## routing, composition (not yet built)

Land with their respective Phase D slices (D1, D3 in the sub-plan).
