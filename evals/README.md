# Evals

One suite per LLM stage. One stage exists today — `jd_extraction`. Scoring arrives in Phase C (D9 splits extraction from scoring so facts and judgment regress independently) and routing/composition in Phase D (D10). Each suite is a JSON case file plus a grading function in `jscc/evals.py`.

## jd_extraction (Slice B1)

`evals/jd_extraction/cases.json` — 15 hand-authored JDs (fictional companies, no real personal data) with expected `ExtractedJD` fields.

Grading (`grade_extraction` in `jscc/evals.py`):
- **Exact match:** `level`, `remote_policy`.
- **Set equality (order-independent):** `must_have_skills`.
- **Presence-only:** `comp_band` — both-None or both-not-None; exact dollar figures aren't graded because they're too brittle to pin a prompt to.
- **Prose, not graded here:** `responsibilities_summary` — checked for non-empty only. Real quality grading (tone, no hallucinated facts) is an LLM-judge rubric, deferred until there's a real prompt worth judging.

Run: `python -m jscc eval jd_extraction`. Exits non-zero on any failing case — it's meant to gate CI once Slice B2 lands a real prompt.

## Adding a case

Append an object to `cases.json` with a unique `id`, `raw_jd` (never real personal/company data — synthetic or scrubbed only, per D7/D8), and an `expected` dict matching `ExtractedJD`'s fields. Cover both presence and absence of `comp_band` and a mix of `remote_policy` values — the grading logic branches on those.

## fit_scoring, routing, composition (not yet built)

Land with their respective Phase C/D slices (C1, D1, D3 in the sub-plan).
