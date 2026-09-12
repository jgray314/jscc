from __future__ import annotations

import pytest

from jscc.evals import (
    EvalCase,
    JD_EXTRACTION_CASES_PATH,
    RecordingClient,
    RecordingMissing,
    ReplayClient,
    format_eval_summary,
    grade_extraction,
    load_cases,
    run_jd_extraction_evals,
)
from jscc.extraction import extract_jd
from jscc.llm_client import LLMResponse, StubExtractionClient
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

def test_cases_file_has_thirty_three_cases() -> None:
    """25 short (paste-shaped) + 8 long (fetch-shaped) — see decisions-log
    2026-09-11 for the statistical sizing rationale (n=15 gave a ~10-point
    standard error on the pass-rate threshold)."""
    cases = load_cases(JD_EXTRACTION_CASES_PATH)
    assert len(cases) == 33
    assert len({c.id for c in cases}) == 33  # unique ids
    assert sum(1 for c in cases if c.group == "short") == 25
    assert sum(1 for c in cases if c.group == "long") == 8


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
    assert summary.total == 33
    assert summary.passed == 0
    assert all(not r.passed for r in summary.results)
    assert all(r.error is None for r in summary.results)  # stub parses cleanly; grading just fails


def test_format_eval_summary_reports_pass_and_fail() -> None:
    summary = run_jd_extraction_evals(_extract_via_stub, JD_EXTRACTION_CASES_PATH)
    text = format_eval_summary(summary)
    assert "0/33 passed" in text
    assert "[FAIL]" in text


def test_format_eval_summary_reports_group_breakdown() -> None:
    """Regression signal on which distribution broke, not just that it broke
    — the short (paste-shaped) and long (fetch-shaped) cases are different
    enough that a combined number alone can hide which one regressed."""
    summary = run_jd_extraction_evals(_extract_via_stub, JD_EXTRACTION_CASES_PATH)
    text = format_eval_summary(summary)
    assert "long: 0/8 passed (0%)" in text
    assert "short: 0/25 passed (0%)" in text


# ---- title + location grading (gate finding H2) ------------------------------
#
# Both fields are specified by every case in cases.json but were absent from
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


def test_skills_containment_forgives_wording_not_scope() -> None:
    """Same skill in different words (pluralization, a superset phrase)
    should pass; an addition with no matching expected slot should still
    fail -- containment forgives wording, not scope."""
    wording_variants = grade_extraction(
        _case(must_have_skills=["spreadsheets", "GPU hardware", "model deployment"]),
        _extracted(must_have_skills=["spreadsheet fluency", "GPUs", "production model deployment"]),
    )
    assert wording_variants.passed, wording_variants.diffs

    real_addition = grade_extraction(
        _case(must_have_skills=["Python"]),
        _extracted(must_have_skills=["Python", "Kubernetes"]),
    )
    assert not real_addition.passed


def test_skills_alternatives_slot_satisfied_by_either_option() -> None:
    """A closed "X or Y" requirement in the JD is one expected slot with two
    acceptable answers, not two separate required skills."""
    named_first = grade_extraction(
        _case(must_have_skills=[["applied statistics", "data science"]]),
        _extracted(must_have_skills=["applied statistics"]),
    )
    assert named_first.passed, named_first.diffs

    named_second = grade_extraction(
        _case(must_have_skills=[["applied statistics", "data science"]]),
        _extracted(must_have_skills=["data science"]),
    )
    assert named_second.passed, named_second.diffs

    named_neither = grade_extraction(
        _case(must_have_skills=[["applied statistics", "data science"]]),
        _extracted(must_have_skills=["causal inference"]),
    )
    assert not named_neither.passed


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


# ---- replay recordings pin the system prompt too (gate finding H-5) ---------


class _FixedClient:
    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        return LLMResponse(text=self._text, input_tokens=0, output_tokens=0, cost_usd=0.0)


def test_recording_client_calls_on_captured_immediately_per_response() -> None:
    """Gate finding M-12: captures used to live only in `.captured`, written
    to disk after the whole run returned. `on_captured` lets a caller
    persist each response the moment it arrives instead."""
    seen: list[tuple[str, str]] = []
    client = RecordingClient(
        _FixedClient('{"title": "X"}'),
        on_captured=lambda key, text: seen.append((key, text)),
    )
    client.complete(model="m", system="s", user="one")
    client.complete(model="m", system="s", user="two")
    assert len(seen) == 2
    assert seen[0][0] != seen[1][0]  # distinct prompts, distinct keys


def test_recording_client_persists_prior_captures_even_if_a_later_call_raises() -> None:
    """The whole point: a run that dies partway through (a safety refusal,
    a transient API error, Ctrl-C) must not lose captures already paid for."""
    calls = {"n": 0}

    class _FlakyClient:
        def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("transient failure")
            return LLMResponse(text="ok", input_tokens=0, output_tokens=0, cost_usd=0.0)

    persisted: dict[str, str] = {}
    client = RecordingClient(
        _FlakyClient(), on_captured=lambda key, text: persisted.update({key: text})
    )
    client.complete(model="m", system="s", user="one")
    with pytest.raises(RuntimeError):
        client.complete(model="m", system="s", user="two")
    assert len(persisted) == 1


def test_save_recording_merges_rather_than_overwrites(tmp_path) -> None:
    """Gate finding M-12: this used to overwrite the file unconditionally,
    so a --record over a subset of cases silently dropped every recording
    not in that run's responses."""
    from jscc.evals import load_recording, save_recording

    path = tmp_path / "recorded.json"
    save_recording({"a": "1"}, path)
    save_recording({"b": "2"}, path)
    assert load_recording(path) == {"a": "1", "b": "2"}


def test_save_recording_lets_a_new_value_replace_an_old_one_for_the_same_key(tmp_path) -> None:
    from jscc.evals import load_recording, save_recording

    path = tmp_path / "recorded.json"
    save_recording({"a": "1"}, path)
    save_recording({"a": "2"}, path)
    assert load_recording(path) == {"a": "2"}


def test_prompt_key_is_prefixed_for_the_scanner(tmp_path) -> None:
    """Gate finding L-14: a bare sha256 hex digest is indistinguishable from
    a phone number to the pre-commit scanner's digit-run heuristic, which is
    why `recorded.json` used to be excluded from scanning wholesale --
    exempting its values along with its keys. The `sha256:` prefix lets the
    scanner strip exactly the key and scan the value like anything else."""
    client = RecordingClient(_FixedClient('{"title": "X"}'))
    response = client.complete(model="m", system="s", user="one")
    (key,) = client.captured.keys()
    assert key.startswith("sha256:")
    assert len(key) == len("sha256:") + 64
    assert response.text == '{"title": "X"}'


def test_replay_key_changes_when_the_system_prompt_changes() -> None:
    """The whole point of keying on the prompt rather than the case id: a
    recording made under one system prompt must not silently answer for a
    different one. Before this fix, `_prompt_key` hashed `user` alone, so
    this replayed the same recording no matter what `system` said."""
    recorder = RecordingClient(_FixedClient('{"title": "X"}'))
    recorder.complete(model="m", system="original system prompt", user="a raw jd")

    replayer = ReplayClient(recorder.captured)
    # Same model and user, different system -- must not find the recording.
    with pytest.raises(RecordingMissing):
        replayer.complete(model="m", system="a different system prompt", user="a raw jd")

    # Unchanged inputs still replay.
    replayer.complete(model="m", system="original system prompt", user="a raw jd")


def test_ordinary_extraction_errors_still_count_as_failed_cases() -> None:
    """Only the D7/D8 boundary exceptions escape — prompt bugs still grade."""
    def broken(raw_text: str) -> ExtractedJD:
        raise ValueError("model returned nonsense")

    summary = run_jd_extraction_evals(broken)
    assert summary.total == 33
    assert summary.passed == 0
    assert all(r.error for r in summary.results)
