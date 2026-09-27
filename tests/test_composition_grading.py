"""Deterministic composition grading (D4b).

Each check has its own boundary tests so a failing case's diff names the check
that failed. Tone and overall quality are deliberately not graded here.
"""

from __future__ import annotations

import pytest

from jscc.evals import CompositionEvalCase, grade_composition
from jscc.models import DraftEmail

GOOD_BODY = (
    "Thank you for taking the time to speak with me today about the Engineering Manager "
    "role at Test Co. I really enjoyed learning more about the team and the scope of the "
    "work, and I'm still very interested in the role. Please let me know if there's "
    "anything else I can share from my side.\n\nBest,"
)
GOOD_SUBJECT = "Thanks for speaking today"


def _case(**overrides) -> CompositionEvalCase:
    fields = dict(
        id="t1",
        application={
            "id": "app-t1",
            "title": "Engineering Manager",
            "company": "Test Co",
            "stage": "screen",
        },
        history=[
            {
                "id": "int-t1",
                "application_id": "app-t1",
                "type": "screen",
                "occurred_at": "2026-09-01T10:00:00Z",
                "notes": "Recruiter screen. Next step is a call on Thursday at 4pm.",
            }
        ],
        intent="post_screen_thank_you",
        style_samples=["Thanks again for the conversation today, it was really useful."],
    )
    fields.update(overrides)
    return CompositionEvalCase(**fields)


def _grade(subject=GOOD_SUBJECT, body=GOOD_BODY, **case_overrides):
    return grade_composition(_case(**case_overrides), DraftEmail(subject=subject, body=body))


def _failed(result) -> set[str]:
    return {d.field for d in result.diffs}


def _flagged(result) -> set[str]:
    """Fields that matched at all, whether they failed the case or only advised."""
    return _failed(result) | {a.field for a in result.advisories}


def _padded_body(reused: str, total_words: int) -> str:
    """A body of exactly `total_words` words: filler plus the reused sentence
    verbatim, so a test can put the reused share at a precise percentage. The
    filler ends with a period so the reused text starts its own sentence (an
    unpunctuated run would make a capitalized reused word look like a
    mid-sentence invented name)."""
    filler = _words(total_words - len(reused.split()))
    return f"{filler}. {reused}"


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def test_a_good_draft_passes_every_check() -> None:
    result = _grade()
    assert result.passed, result.diffs


# ---- presence ---------------------------------------------------------------------


def test_empty_subject_and_empty_body_fail_by_name() -> None:
    assert "subject" in _failed(_grade(subject=""))
    assert "body" in _failed(_grade(body="   "))


# ---- placeholders -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["[Your Name]", "{{first_name}}", "<recipient>", "[redacted-email]", "was redacted here"],
)
def test_placeholders_and_redaction_tokens_fail_in_body_or_subject(bad: str) -> None:
    assert "placeholder" in _failed(_grade(body=GOOD_BODY + f"\n{bad}"))
    assert "placeholder" in _failed(_grade(subject=f"Thanks {bad}"))


# ---- length -----------------------------------------------------------------------


def test_body_length_bounds_are_25_to_160_words() -> None:
    assert "body_length" in _failed(_grade(body=_words(24)))
    assert "body_length" not in _failed(_grade(body=_words(25)))
    assert "body_length" not in _failed(_grade(body=_words(160)))
    assert "body_length" in _failed(_grade(body=_words(161)))


def test_subject_length_bound_is_10_words() -> None:
    assert "subject_length" not in _failed(_grade(subject=_words(10)))
    assert "subject_length" in _failed(_grade(subject=_words(11)))


# ---- invented numbers --------------------------------------------------------------


def test_a_number_not_in_the_facts_fails() -> None:
    result = _grade(body=GOOD_BODY + " Could we talk on the 27th?")
    assert "invented_number" in _failed(result)


def test_numbers_present_in_history_pass_including_times_and_dates() -> None:
    body = GOOD_BODY + " Thursday at 4pm works, and I'll note it was September 1."
    assert "invented_number" not in _failed(_grade(body=body))


def test_leading_zeros_are_normalized() -> None:
    """The fixture stores the date as 2026-09-01; a draft saying 'September 1' or
    '9/1' must match it."""
    assert "invented_number" not in _failed(_grade(body=GOOD_BODY + " On 9/1 we spoke."))


def test_style_sample_numbers_do_not_count_as_facts() -> None:
    """Style samples are voice, not facts: a draft adopting '7pm' from one is
    inventing a time."""
    case_kwargs = dict(style_samples=["Tuesday at 7pm works well for me."])
    result = _grade(body=GOOD_BODY + " Tuesday at 7pm works.", **case_kwargs)
    assert "invented_number" in _failed(result)


# ---- invented weekdays and relative dates -------------------------------------------
#
# The composer is never told what day it is, so it has no way to correctly
# state a weekday or a phrase asserting elapsed time -- these check the same
# facts-corpus grounding as invented numbers, above.


def test_a_weekday_not_in_the_facts_fails() -> None:
    """The fixture's only weekday is Thursday (in the history note); Friday is
    not grounded anywhere."""
    result = _grade(body=GOOD_BODY + " Friday works well for me.")
    assert "invented_weekday" in _failed(result)


def test_a_weekday_present_in_the_facts_passes() -> None:
    result = _grade(body=GOOD_BODY + " Thursday at 4pm works well for me.")
    assert "invented_weekday" not in _failed(result)


def test_a_possessive_weekday_in_the_facts_still_counts_as_grounded() -> None:
    """A fact phrased as \"Thursday's screen\" must still ground a draft that
    says plain \"Thursday\" -- the possessive apostrophe should not hide the
    word from the grounding check."""
    case_kwargs = dict(
        history=[
            {
                "id": "int-t1",
                "application_id": "app-t1",
                "type": "custom",
                "occurred_at": "2026-09-01T10:00:00Z",
                "notes": "Recruiter asked to move Thursday's screen to 4pm.",
            }
        ]
    )
    result = _grade(body=GOOD_BODY + " Thursday at 4pm works well for me.", **case_kwargs)
    assert "invented_weekday" not in _failed(result)


def test_style_sample_weekdays_do_not_count_as_facts() -> None:
    case_kwargs = dict(style_samples=["Friday works great for me, thanks."])
    result = _grade(body=GOOD_BODY + " Friday works.", **case_kwargs)
    assert "invented_weekday" in _failed(result)


def test_a_relative_date_word_not_in_the_facts_fails() -> None:
    result = _grade(body=GOOD_BODY + " Thanks again for yesterday.")
    assert "invented_relative_date" in _failed(result)


def test_a_relative_date_word_present_in_the_facts_passes() -> None:
    case_kwargs = dict(
        history=[
            {
                "id": "int-t1",
                "application_id": "app-t1",
                "type": "custom",
                "occurred_at": "2026-09-01T10:00:00Z",
                "notes": "Recruiter said next step would come last week.",
            }
        ]
    )
    result = _grade(body=GOOD_BODY + " Thanks for the update last week.", **case_kwargs)
    assert "invented_relative_date" not in _failed(result)


def test_today_and_tonight_are_not_flagged() -> None:
    """Thanking someone for a call 'today' is the ordinary same-day case (the
    style samples use it too) and not an elapsed-time claim the composer could
    get wrong, unlike 'yesterday' or 'last week'."""
    result = _grade(body=GOOD_BODY + " Thanks again for making time tonight.")
    assert "invented_relative_date" not in _failed(result)


# ---- verbatim style reuse ----------------------------------------------------------
#
# A single reused closing line is ordinary shortness in a short email, not a template,
# so severity scales with how much of the body it makes up rather than firing on any
# match: under 20% passes clean, 20-30% is advisory, over 30% fails.

_REUSED_SENTENCE = "Thanks again for the conversation today, it was really useful."  # 10 words


def test_copying_a_style_sample_sentence_under_20_percent_of_the_body_passes_clean() -> None:
    body = _padded_body(_REUSED_SENTENCE, 60)  # 10/60 = 16.7%
    assert "style_reuse" not in _flagged(_grade(body=body))


def test_copying_a_style_sample_sentence_between_20_and_30_percent_is_advisory() -> None:
    body = _padded_body(_REUSED_SENTENCE, 40)  # 10/40 = 25%
    result = _grade(body=body)
    assert "style_reuse" not in _failed(result)
    assert "style_reuse" in {a.field for a in result.advisories}


def test_copying_a_style_sample_sentence_over_30_percent_of_the_body_fails() -> None:
    body = _padded_body(_REUSED_SENTENCE, 32)  # 10/32 = 31.25%
    assert "style_reuse" in _failed(_grade(body=body))


def test_reuse_check_is_case_and_whitespace_insensitive() -> None:
    messy = "thanks  again for the CONVERSATION today,  it was really useful"
    body = _padded_body(messy, 32)
    assert "style_reuse" in _failed(_grade(body=body))


def test_short_sample_sentences_may_be_echoed() -> None:
    case_kwargs = dict(style_samples=["Thanks so much."])
    assert "style_reuse" not in _failed(_grade(body=GOOD_BODY + " Thanks so much.", **case_kwargs))


# ---- invented names ----------------------------------------------------------------


def test_an_invented_capitalized_name_fails() -> None:
    body = GOOD_BODY + " Please say hello to Sarah for me."
    assert "invented_name" in _failed(_grade(body=body))


def test_capitalized_words_from_the_facts_sentence_starts_and_calendar_words_pass() -> None:
    body = GOOD_BODY + " Thursday works. Recruiter notes say so. See you in September."
    assert "invented_name" not in _failed(_grade(body=body))


def test_title_case_subjects_are_not_name_checked() -> None:
    assert "invented_name" not in _failed(_grade(subject="Thank You For Your Time Today"))


# ---- per-case expectations ---------------------------------------------------------


def test_must_include_needs_one_hit_from_every_group() -> None:
    expectations = dict(must_include=[["thank"], ["4pm", "four"]])
    assert "must_include" in _failed(_grade(**expectations))
    ok = _grade(body=GOOD_BODY + " Thursday at 4pm is fine.", **expectations)
    assert "must_include" not in _failed(ok)


def test_must_include_matches_case_insensitively_across_subject_and_body() -> None:
    result = _grade(subject="Confirming THURSDAY", must_include=[["thursday"]])
    assert "must_include" not in _failed(result)


def test_must_not_include_fails_on_any_hit() -> None:
    result = _grade(must_not_include=["system design", "roadmap"])
    assert "must_not_include" not in _failed(result)
    hit = _grade(body=GOOD_BODY + " The roadmap talk was great.", must_not_include=["roadmap"])
    assert "must_not_include" in _failed(hit)


def test_a_case_without_expectations_gets_only_the_generic_checks() -> None:
    case = _case()
    assert case.must_include == [] and case.must_not_include == []


# ---- stock phrases: social conventions count as one unit in the reuse check ----------


def test_a_sample_sentence_made_mostly_of_stock_phrases_may_be_echoed() -> None:
    """'wanted to check in' and 'timing for next steps' each count as one unit, so
    the copied run is 4 units, under the 6-unit limit: never even matched, at any
    body length."""
    sample = "Wanted to check in briefly on timing for next steps."
    body = _padded_body(sample, 32)
    assert "style_reuse" not in _flagged(_grade(body=body, style_samples=[sample]))


def test_a_whole_sentence_with_a_stock_phrase_inside_is_still_flagged() -> None:
    sample = "No pressure, just keen to know how the team is thinking about timing."
    body = _padded_body(sample, 32)
    assert "style_reuse" in _failed(_grade(body=body, style_samples=[sample]))


def test_the_limit_is_still_six_units_after_collapsing() -> None:
    """'happy to work around' is one unit, plus five ordinary words: exactly 6 --
    still matched as a copy (at least advisory), not exempted for being borderline."""
    sample = "Happy to work around whatever the team has open."
    body = _padded_body(sample, 32)
    assert "style_reuse" in _flagged(_grade(body=body, style_samples=[sample]))


def test_stock_phrase_matching_ignores_case_and_apostrophes() -> None:
    sample = "Let me know if there's anything else I can do to help."
    messy = "LET ME KNOW if there's anything else I can do to help"
    body = _padded_body(messy, 32)
    assert "style_reuse" in _failed(_grade(body=body, style_samples=[sample]))  # 8 units left


# ---- form-letter guard: advisory only ------------------------------------------------

# GOOD_BODY already uses a few stock phrases, so these tests use a body with none.
NEUTRAL_BODY = (
    "Thanks for speaking with me today about the Engineering Manager role at Test Co. "
    "The conversation gave me a clearer picture of the team and the work ahead, and I "
    "would enjoy continuing it whenever it suits your schedule.\n\nBest,"
)
_STOCK_HEAVY = (
    "Wanted to check in on where things stand. No rush at all, and no pressure. "
    "Looking forward to hearing back. Let me know if you need anything."
)


def test_more_than_four_distinct_stock_phrases_is_advised_not_failed() -> None:
    result = _grade(body=NEUTRAL_BODY + "\n" + _STOCK_HEAVY)
    assert result.passed, result.diffs
    assert [a.field for a in result.advisories] == ["form_letter"]


def test_four_or_fewer_stock_phrases_carry_no_advisory() -> None:
    body = NEUTRAL_BODY + "\nWanted to check in. No rush. No pressure. Looking forward to it."
    assert _grade(body=body).advisories == []


def test_overlapping_phrases_count_once() -> None:
    """'timing for next steps' contains 'next steps'; it is one phrase, not two."""
    body = NEUTRAL_BODY + "\nWanted to check in on timing for next steps. No rush. No pressure."
    assert _grade(body=body).advisories == []


def test_advisories_show_in_the_summary_without_failing_the_case() -> None:
    from jscc.evals import EvalSummary, format_eval_summary

    result = _grade(body=NEUTRAL_BODY + "\n" + _STOCK_HEAVY)
    summary = EvalSummary(total=1, passed=1, results=[result])
    text = format_eval_summary(summary)
    assert "[PASS] t1" in text and "advisory form_letter" in text


# ---- needs_input: the should-escalate cases (D4c) --------------------------------------


def _escalation_case(**overrides):
    return _case(expect_needs_input=True, must_include=[["dietary", "diet"]], **overrides)


def test_a_case_that_expects_escalation_passes_when_the_detail_is_named() -> None:
    draft = DraftEmail(needs_input="The candidate's dietary needs for the onsite lunch.")
    assert grade_composition(_escalation_case(), draft).passed


def test_a_case_that_expects_escalation_fails_when_a_draft_is_written() -> None:
    result = grade_composition(_escalation_case(), DraftEmail(subject=GOOD_SUBJECT, body=GOOD_BODY))
    assert "needs_input" in _failed(result)


def test_escalation_must_name_the_missing_detail_through_must_include() -> None:
    result = grade_composition(_escalation_case(), DraftEmail(needs_input="More information."))
    assert "must_include" in _failed(result)


def test_escalation_is_not_held_to_draft_checks() -> None:
    """No subject, body, length or style checks run on a question."""
    result = grade_composition(_escalation_case(), DraftEmail(needs_input="Dietary needs?"))
    assert result.passed, result.diffs


def test_an_unexpected_escalation_fails_a_draftable_case() -> None:
    result = grade_composition(_case(), DraftEmail(needs_input="Anything else?"))
    assert "unexpected_needs_input" in _failed(result)
    assert "subject" not in _failed(result)  # one clear cause, not a pile of missing-draft noise
