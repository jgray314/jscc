from __future__ import annotations

from jscc.evals import (
    EvalCase,
    JD_EXTRACTION_CASES_PATH,
    format_eval_summary,
    grade_extraction,
    load_cases,
    run_jd_extraction_evals,
)
from jscc.extraction import extract_jd
from jscc.llm_client import StubExtractionClient
from jscc.models import ExtractedJD


def _case(**expected_overrides) -> EvalCase:
    expected = dict(
        title="Senior Backend Engineer",
        level="senior",
        comp_band="$180,000-$220,000",
        location=None,
        remote_policy="remote",
        must_have_skills=["Python", "PostgreSQL"],
        responsibilities_summary="Owns billing services.",
    )
    expected.update(expected_overrides)
    return EvalCase(id="t1", raw_jd="raw text", expected=expected)


def _extracted(**overrides) -> ExtractedJD:
    fields = dict(
        title="Senior Backend Engineer",
        level="senior",
        comp_band="$180,000-$220,000",
        location=None,
        remote_policy="remote",
        must_have_skills=["Python", "PostgreSQL"],
        responsibilities_summary="Owns billing services.",
    )
    fields.update(overrides)
    return ExtractedJD(**fields)


# ---- fixture file ---------------------------------------------------------------

def test_cases_file_has_fifteen_cases() -> None:
    cases = load_cases(JD_EXTRACTION_CASES_PATH)
    assert len(cases) == 15
    assert len({c.id for c in cases}) == 15  # unique ids


def test_cases_file_covers_comp_band_presence_and_absence() -> None:
    cases = load_cases(JD_EXTRACTION_CASES_PATH)
    has_comp = [c for c in cases if c.expected.get("comp_band") is not None]
    no_comp = [c for c in cases if c.expected.get("comp_band") is None]
    assert has_comp and no_comp


# ---- grading ----------------------------------------------------------------

def test_grade_extraction_exact_match_passes() -> None:
    result = grade_extraction(_case(), _extracted())
    assert result.passed, result.diffs


def test_grade_extraction_level_mismatch_fails() -> None:
    result = grade_extraction(_case(level="senior"), _extracted(level="staff"))
    assert not result.passed
    assert any(d.field == "level" for d in result.diffs)


def test_grade_extraction_skills_set_equality_ignores_order() -> None:
    result = grade_extraction(
        _case(must_have_skills=["Python", "PostgreSQL"]),
        _extracted(must_have_skills=["PostgreSQL", "Python"]),
    )
    assert result.passed


def test_grade_extraction_comp_band_presence_mismatch_fails() -> None:
    result = grade_extraction(
        _case(comp_band="$100k-$150k"), _extracted(comp_band=None)
    )
    assert not result.passed
    assert any(d.field == "comp_band" for d in result.diffs)


def test_grade_extraction_comp_band_exact_figure_not_required() -> None:
    """Presence-only per the eval strategy doc — dollar figures are too brittle."""
    result = grade_extraction(
        _case(comp_band="$100k-$150k"), _extracted(comp_band="$110k-$140k")
    )
    assert result.passed


def test_grade_extraction_empty_prose_fails() -> None:
    result = grade_extraction(_case(), _extracted(responsibilities_summary=""))
    assert not result.passed
    assert any(d.field == "responsibilities_summary" for d in result.diffs)


# ---- harness against StubExtractionClient (no API key in B2) ------------------

def _extract_via_stub(raw_text: str) -> ExtractedJD:
    return extract_jd(raw_text, client=StubExtractionClient())


def test_run_jd_extraction_evals_against_stub_client() -> None:
    """StubExtractionClient returns a fixed placeholder, not a real extraction.
    Every case is expected to fail on structural fields — that's the honest
    result until an ANTHROPIC_API_KEY is set and the prompt is iterated,
    not a regression."""
    summary = run_jd_extraction_evals(_extract_via_stub)
    assert summary.total == 15
    assert summary.passed == 0
    assert all(not r.passed for r in summary.results)
    assert all(r.error is None for r in summary.results)  # stub parses cleanly; grading just fails


def test_format_eval_summary_reports_pass_and_fail() -> None:
    summary = run_jd_extraction_evals(_extract_via_stub, JD_EXTRACTION_CASES_PATH)
    text = format_eval_summary(summary)
    assert "0/15 passed" in text
    assert "[FAIL]" in text
