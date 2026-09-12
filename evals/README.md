# Evals

One suite per LLM stage. One stage exists today — `jd_extraction`. Scoring arrives in Phase C (D9 splits extraction from scoring so facts and judgment regress independently) and routing/composition in Phase D (D10). Each suite is a JSON case file plus a grading function in `jscc/evals.py`.

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

## fit_scoring, routing, composition (not yet built)

Land with their respective Phase C/D slices (C1, D1, D3 in the sub-plan).
