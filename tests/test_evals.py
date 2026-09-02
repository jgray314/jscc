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


# ---- title + location grading (gate finding H2) ------------------------------
#
# Both fields are specified by all 15 cases in cases.json but were absent from
# every graded-field tuple, so the harness read the expectations and dropped
# them. `title` is the only extracted field with a production consumer.


def test_title_mismatch_fails() -> None:
    result = grade_extraction(
        _case(title="Senior Backend Engineer"), _extracted(title="Staff Frontend Engineer")
    )
    assert not result.passed
    assert any(d.field == "title" for d in result.diffs)


def test_title_ignores_case_and_whitespace_noise() -> None:
    """Formatting variance is not an extraction error; wording still is."""
    result = grade_extraction(
        _case(title="Senior Backend Engineer"),
        _extracted(title="  senior   backend engineer "),
    )
    assert result.passed, result.diffs


def test_location_presence_mismatch_fails() -> None:
    """A remote-only role should yield null — presence carries real signal."""
    result = grade_extraction(_case(location=None), _extracted(location="Austin, TX"))
    assert not result.passed
    assert any(d.field == "location" for d in result.diffs)


def test_location_missing_when_expected_fails() -> None:
    result = grade_extraction(_case(location="Austin, TX"), _extracted(location=None))
    assert not result.passed


def test_location_wrong_city_fails() -> None:
    result = grade_extraction(_case(location="Denver"), _extracted(location="Seattle, WA"))
    assert not result.passed


def test_location_accepts_a_more_specific_answer() -> None:
    """"Denver" vs "Denver, CO" is not an extraction failure."""
    result = grade_extraction(_case(location="Denver"), _extracted(location="Denver, CO"))
    assert result.passed, result.diffs


def test_skills_set_ignores_casing_but_not_wording() -> None:
    ok = grade_extraction(
        _case(must_have_skills=["Python", "PostgreSQL"]),
        _extracted(must_have_skills=["python", "postgresql"]),
    )
    assert ok.passed, ok.diffs

    bad = grade_extraction(
        _case(must_have_skills=["Python", "PostgreSQL"]),
        _extracted(must_have_skills=["Python", "Postgres"]),
    )
    assert not bad.passed


def test_every_extracted_jd_field_is_graded_or_explicitly_prose() -> None:
    """Guards the H2 class of bug generally: a field added to ExtractedJD
    later must be given a rule, not silently ignored."""
    from jscc.evals import _GRADED_FIELDS, _PROSE_FIELDS

    covered = set(_GRADED_FIELDS) | set(_PROSE_FIELDS)
    assert set(ExtractedJD.model_fields) == covered


# ---- safety exceptions must not be graded away (gate finding M3) -------------


def test_sanitizer_refusal_propagates_instead_of_counting_as_a_failed_case() -> None:
    import pytest

    from jscc.sanitizer import SanitizerRefusal

    def refusing(raw_text: str) -> ExtractedJD:
        raise SanitizerRefusal("payload flagged contains_personal")

    with pytest.raises(SanitizerRefusal):
        run_jd_extraction_evals(refusing)


def test_llm_send_error_propagates() -> None:
    import pytest

    from jscc.sanitizer import LLMSendError

    def failing(raw_text: str) -> ExtractedJD:
        raise LLMSendError("verify() failed at the send boundary")

    with pytest.raises(LLMSendError):
        run_jd_extraction_evals(failing)


def test_ordinary_extraction_errors_still_count_as_failed_cases() -> None:
    """Only the D7/D8 boundary exceptions escape — prompt bugs still grade."""
    def broken(raw_text: str) -> ExtractedJD:
        raise ValueError("model returned nonsense")

    summary = run_jd_extraction_evals(broken)
    assert summary.total == 15
    assert summary.passed == 0
    assert all(r.error for r in summary.results)
