from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jscc.composition import (
    COMPOSITION_FEATURE,
    CompositionParseError,
    compose_followup,
)
from jscc.llm_client import COMPOSITION_MODEL, SCORING_MODEL, LLMResponse, StubCompositionClient
from jscc.models import Application, Interaction
from jscc.sanitizer import SanitizerRefusal
from jscc.storage import _connect, _init_db, list_llm_calls


class _FakeClient:
    """Records the call it received and returns a canned response."""

    def __init__(self, response_text: str, *, input_tokens=10, output_tokens=5, cost_usd=0.001):
        self.response_text = response_text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.calls: list[dict] = []

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        self.calls.append({"model": model, "system": system, "user": user})
        return LLMResponse(
            text=self.response_text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
        )


_VALID_RESPONSE = json.dumps(
    {"subject": "Thank you", "body": "Thanks for the time today.\n\nBest,\nSample"}
)


def _app(**overrides) -> Application:
    fields = dict(title="Engineering Manager", company="Acme Corp", stage="onsite")
    fields.update(overrides)
    return Application(**fields)


def _history(**overrides) -> list[Interaction]:
    fields = dict(
        application_id="app-1",
        type="onsite",
        occurred_at="2026-09-10T15:00:00Z",
        notes="Full-loop onsite.",
    )
    fields.update(overrides)
    return [Interaction(**fields)]


@pytest.fixture()
def conn(tmp_path: Path):
    c = _connect(tmp_path / "test.db")
    _init_db(c)
    yield c
    c.close()


# ---- model choice ---------------------------------------------------------------


def test_composition_uses_the_scoring_tier_model() -> None:
    """D10: composition is a judgment call, Sonnet territory like scoring."""
    assert COMPOSITION_MODEL == SCORING_MODEL


# ---- stub fallback (no API key) -------------------------------------------------


def test_compose_followup_uses_stub_by_default_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    draft = compose_followup(_app(), _history(), "post_interview_thank_you", [])
    assert "StubCompositionClient" in draft.body


def test_stub_draft_fails_the_presence_grader_so_an_unconfigured_run_reads_as_failing() -> None:
    """The stub must not read as a passing draft: an empty subject keeps the
    eval at 0% instead of a misleading 100% on presence-only grading."""
    draft = compose_followup(
        _app(), _history(), "cadence_nudge", [], client=StubCompositionClient()
    )
    assert draft.subject == ""


# ---- request wiring ------------------------------------------------------------


def test_request_carries_application_history_intent_and_style_samples() -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    compose_followup(
        _app(title="Staff Engineer"),
        _history(notes="Thanks for the onsite."),
        "post_interview_thank_you",
        ["Really enjoyed the conversation."],
        client=fake,
    )
    sent = json.loads(fake.calls[0]["user"])
    assert sent["application"]["title"] == "Staff Engineer"
    assert sent["history"][0]["notes"] == "Thanks for the onsite."
    assert sent["intent"] == "post_interview_thank_you"
    assert sent["style_samples"] == ["Really enjoyed the conversation."]
    assert fake.calls[0]["model"] == COMPOSITION_MODEL
    assert "JSON" in fake.calls[0]["system"]


def test_prompt_asks_for_a_subject_of_eight_words_or_fewer() -> None:
    """The grader allows 10; the prompt aims lower so a normal draft has slack."""
    from jscc.composition import COMPOSITION_SYSTEM_PROMPT

    assert "8 words or fewer" in COMPOSITION_SYSTEM_PROMPT


def test_user_prompt_is_deterministic_for_record_replay_keying() -> None:
    a, b = _FakeClient(_VALID_RESPONSE), _FakeClient(_VALID_RESPONSE)
    app = _app(created_at="2026-09-01T00:00:00Z", updated_at="2026-09-10T00:00:00Z")
    args = (app, _history(), "cadence_nudge", ["Checking in."])
    compose_followup(*args, client=a)
    compose_followup(*args, client=b)
    assert a.calls[0]["user"] == b.calls[0]["user"]


# ---- response parsing ----------------------------------------------------------


def test_parses_a_valid_draft() -> None:
    draft = compose_followup(_app(), _history(), "x", [], client=_FakeClient(_VALID_RESPONSE))
    assert draft.subject == "Thank you"
    assert draft.body.startswith("Thanks for the time")


def test_parses_a_fenced_draft() -> None:
    fenced = f"```json\n{_VALID_RESPONSE}\n```"
    draft = compose_followup(_app(), _history(), "x", [], client=_FakeClient(fenced))
    assert draft.subject == "Thank you"


def test_raises_on_invalid_json() -> None:
    with pytest.raises(CompositionParseError):
        compose_followup(_app(), _history(), "x", [], client=_FakeClient("not json at all"))


def test_raises_on_json_missing_a_required_field() -> None:
    with pytest.raises(CompositionParseError):
        compose_followup(
            _app(), _history(), "x", [], client=_FakeClient(json.dumps({"subject": "only"}))
        )


def test_raises_on_truncated_response() -> None:
    class _Truncated:
        def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
            return LLMResponse(
                text='{"subject": "Tha',
                input_tokens=0,
                output_tokens=5,
                cost_usd=0.0,
                stop_reason="max_tokens",
            )

    with pytest.raises(CompositionParseError, match="truncated"):
        compose_followup(_app(), _history(), "x", [], client=_Truncated())


# ---- instrumentation (D5) ------------------------------------------------------


def test_records_llm_call_when_conn_provided(conn: sqlite3.Connection) -> None:
    fake = _FakeClient(_VALID_RESPONSE, input_tokens=42, output_tokens=17, cost_usd=0.0055)
    compose_followup(_app(), _history(), "x", [], conn=conn, client=fake)
    (record,) = list_llm_calls(conn)
    assert record.feature == COMPOSITION_FEATURE == "composition"
    assert record.model == COMPOSITION_MODEL
    assert (record.input_tokens, record.output_tokens, record.cost_usd) == (42, 17, 0.0055)


def test_eval_feature_is_ledgered_separately(conn: sqlite3.Connection) -> None:
    compose_followup(
        _app(),
        _history(),
        "x",
        [],
        conn=conn,
        client=_FakeClient(_VALID_RESPONSE),
        feature="composition_eval",
    )
    assert list_llm_calls(conn)[0].feature == "composition_eval"


def test_unknown_feature_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown instrumentation feature"):
        compose_followup(
            _app(),
            _history(),
            "x",
            [],
            conn=conn,
            client=_FakeClient(_VALID_RESPONSE),
            feature="nope",
        )


def test_unparseable_response_still_records_the_ledger_row(conn: sqlite3.Connection) -> None:
    client = _FakeClient("not json", input_tokens=1234, output_tokens=56, cost_usd=0.0042)
    with pytest.raises(CompositionParseError):
        compose_followup(_app(), _history(), "x", [], conn=conn, client=client)
    (record,) = list_llm_calls(conn)
    assert (record.input_tokens, record.cost_usd) == (1234, 0.0042)


def test_conn_less_call_does_not_require_a_database() -> None:
    draft = compose_followup(_app(), _history(), "x", [], client=_FakeClient(_VALID_RESPONSE))
    assert draft.subject


# ---- redaction reaches the client ----------------------------------------------


_CONTACT_EMAIL = "dana.reyes" + "@" + "riftcloud.example"


def test_client_receives_redacted_history_and_style_samples() -> None:
    client = _FakeClient(_VALID_RESPONSE)
    compose_followup(
        _app(),
        _history(notes=f"Recruiter is {_CONTACT_EMAIL}, said the loop is done."),
        "x",
        [f"Reach me at {_CONTACT_EMAIL} any time."],
        client=client,
    )
    sent = client.calls[0]["user"]
    assert _CONTACT_EMAIL not in sent
    assert "[redacted-email]" in sent
    assert "the loop is done" in sent


def test_propagates_sanitizer_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    import jscc.composition as composition_module

    original = composition_module.sanitize_for_llm

    def _force_flag(payload, **kwargs):
        payload["contains_personal"] = True
        return original(payload, **kwargs)

    monkeypatch.setattr(composition_module, "sanitize_for_llm", _force_flag)
    with pytest.raises(SanitizerRefusal):
        compose_followup(_app(), _history(), "x", [], client=StubCompositionClient())


# ---- needs_input safety net (D4c) -------------------------------------------------


def test_prompt_offers_a_needs_input_escape_hatch() -> None:
    from jscc.composition import COMPOSITION_SYSTEM_PROMPT

    assert '"needs_input"' in COMPOSITION_SYSTEM_PROMPT
    assert "do not guess" in COMPOSITION_SYSTEM_PROMPT.lower()


def test_parses_a_needs_input_response_into_an_empty_draft() -> None:
    client = _FakeClient(json.dumps({"needs_input": "Dietary needs for the onsite lunch."}))
    draft = compose_followup(_app(), _history(), "logistics_confirmation", [], client=client)
    assert draft.needs_input == "Dietary needs for the onsite lunch."
    assert draft.subject == "" and draft.body == ""


def test_a_normal_draft_has_no_needs_input() -> None:
    draft = compose_followup(
        _app(), _history(), "post_interview_thank_you", [], client=_FakeClient(_VALID_RESPONSE)
    )
    assert draft.needs_input is None


def test_a_null_needs_input_alongside_a_draft_is_a_normal_draft() -> None:
    text = json.dumps({"subject": "Hi", "body": "Thanks.", "needs_input": None})
    draft = compose_followup(_app(), _history(), "x", [], client=_FakeClient(text))
    assert draft.needs_input is None and draft.subject == "Hi"


def test_needs_input_wins_when_the_model_also_returns_a_draft() -> None:
    """Safe direction: a draft built on a guess is exactly what the field exists
    to stop, so it is dropped rather than shipped next to the question."""
    text = json.dumps({"subject": "Hi", "body": "I have no dietary needs.", "needs_input": "Diet?"})
    draft = compose_followup(_app(), _history(), "x", [], client=_FakeClient(text))
    assert draft.needs_input == "Diet?"
    assert draft.subject == "" and draft.body == ""


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_needs_input_without_a_draft_is_a_parse_error(blank) -> None:
    text = json.dumps({"needs_input": blank})
    with pytest.raises(CompositionParseError):
        compose_followup(_app(), _history(), "x", [], client=_FakeClient(text))


def test_a_non_string_needs_input_is_a_parse_error() -> None:
    text = json.dumps({"needs_input": ["diet"]})
    with pytest.raises(CompositionParseError):
        compose_followup(_app(), _history(), "x", [], client=_FakeClient(text))
