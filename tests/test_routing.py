from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jscc.llm_client import LLMResponse, StubRoutingClient
from jscc.models import Application, Interaction, RoutingClassification
from jscc.routing import ROUTING_MODEL, RoutingParseError, route_followup
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


_VALID_ROUTINE_RESPONSE = json.dumps(
    {
        "classification": "routine",
        "intent": "post_interview_thank_you",
        "reason": None,
        "considerations": [],
    }
)
_VALID_NON_ROUTINE_RESPONSE = json.dumps(
    {
        "classification": "non_routine",
        "intent": None,
        "reason": "compensation negotiation",
        "considerations": ["comp band", "leverage"],
    }
)


def _app(**overrides) -> Application:
    fields = dict(title="Engineering Manager", company="Acme Corp", stage="screen")
    fields.update(overrides)
    return Application(**fields)


def _history(**overrides) -> list[Interaction]:
    fields = dict(
        application_id="app-1",
        type="screen",
        occurred_at="2026-09-01T10:00:00Z",
        notes="Recruiter screen.",
    )
    fields.update(overrides)
    return [Interaction(**fields)]


@pytest.fixture()
def conn(tmp_path: Path):
    c = _connect(tmp_path / "test.db")
    _init_db(c)
    yield c
    c.close()


# ---- stub fallback (no API key) ------------------------------------------------


def test_route_followup_uses_stub_by_default_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    decision = route_followup(_app(), _history())
    assert decision.classification == RoutingClassification.non_routine
    assert "StubRoutingClient" in (decision.reason or "")


def test_route_followup_accepts_explicit_client() -> None:
    decision = route_followup(_app(), _history(), client=StubRoutingClient())
    assert decision.classification == RoutingClassification.non_routine


# ---- request wiring --------------------------------------------------------------


def test_route_followup_sends_application_and_history_in_user_prompt() -> None:
    fake = _FakeClient(_VALID_ROUTINE_RESPONSE)
    route_followup(
        _app(title="Staff Engineer"), _history(notes="Thanks for the onsite."), client=fake
    )
    sent = json.loads(fake.calls[0]["user"])
    # The extracted title is withheld from the router (see `application_for_routing`);
    # the fields that do bear on the decision are sent.
    assert sent["application"]["title"] == ""
    assert sent["application"]["stage"] == _app().stage
    assert sent["history"][0]["notes"] == "Thanks for the onsite."
    assert fake.calls[0]["model"] == ROUTING_MODEL
    assert "JSON" in fake.calls[0]["system"]


# ---- response parsing -------------------------------------------------------------


def test_route_followup_parses_routine_response() -> None:
    fake = _FakeClient(_VALID_ROUTINE_RESPONSE)
    decision = route_followup(_app(), _history(), client=fake)
    assert decision.classification == RoutingClassification.routine
    assert decision.intent == "post_interview_thank_you"


def test_route_followup_parses_non_routine_response() -> None:
    fake = _FakeClient(_VALID_NON_ROUTINE_RESPONSE)
    decision = route_followup(_app(), _history(), client=fake)
    assert decision.classification == RoutingClassification.non_routine
    assert decision.reason == "compensation negotiation"
    assert decision.considerations == ["comp band", "leverage"]


def test_route_followup_raises_on_invalid_json() -> None:
    fake = _FakeClient("not valid json at all")
    with pytest.raises(RoutingParseError, match="not valid JSON"):
        route_followup(_app(), _history(), client=fake)


def test_route_followup_raises_on_json_missing_required_field() -> None:
    fake = _FakeClient(json.dumps({"intent": "x"}))  # missing classification
    with pytest.raises(RoutingParseError):
        route_followup(_app(), _history(), client=fake)


def test_route_followup_raises_on_truncated_response() -> None:
    class _TruncatedClient:
        def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
            return LLMResponse(
                text='{"classification": "rou',
                input_tokens=0,
                output_tokens=5,
                cost_usd=0.0,
                stop_reason="max_tokens",
            )

    with pytest.raises(RoutingParseError, match="truncated"):
        route_followup(_app(), _history(), client=_TruncatedClient())


# ---- instrumentation (D5) ---------------------------------------------------------


def test_route_followup_records_llm_call_when_conn_provided(conn: sqlite3.Connection) -> None:
    fake = _FakeClient(_VALID_ROUTINE_RESPONSE, input_tokens=42, output_tokens=17, cost_usd=0.0055)
    route_followup(_app(), _history(), conn=conn, client=fake)

    records = list_llm_calls(conn)
    assert len(records) == 1
    record = records[0]
    assert record.feature == "routing"
    assert record.model == ROUTING_MODEL
    assert record.input_tokens == 42
    assert record.output_tokens == 17
    assert record.cost_usd == 0.0055


def test_route_followup_without_conn_does_not_require_a_database() -> None:
    fake = _FakeClient(_VALID_ROUTINE_RESPONSE)
    decision = route_followup(_app(), _history(), client=fake)  # no conn — must not raise
    assert decision.classification == RoutingClassification.routine


# ---- the ledger records billed calls that fail to parse (same shape as M2) ---


def test_unparseable_response_still_records_the_ledger_row(conn: sqlite3.Connection) -> None:
    client = _FakeClient("not json at all", input_tokens=1234, output_tokens=56, cost_usd=0.0042)
    with pytest.raises(RoutingParseError):
        route_followup(_app(), _history(), conn=conn, client=client)

    calls = list_llm_calls(conn)
    assert len(calls) == 1
    assert calls[0].input_tokens == 1234
    assert calls[0].output_tokens == 56
    assert calls[0].cost_usd == pytest.approx(0.0042)


def test_conn_less_parse_failure_still_raises() -> None:
    with pytest.raises(RoutingParseError):
        route_followup(_app(), _history(), client=_FakeClient("not json at all"))


# ---- redaction actually reaches the client (same C1/C2a-shaped gap) ---------


_CONTACT_EMAIL = "dana.reyes" + "@" + "riftcloud.example"


def test_client_receives_redacted_text_not_the_raw_history(conn: sqlite3.Connection) -> None:
    client = _FakeClient(_VALID_ROUTINE_RESPONSE)
    route_followup(
        _app(),
        _history(notes=f"Recruiter is {_CONTACT_EMAIL}, said the loop is done."),
        conn=conn,
        client=client,
    )

    sent = client.calls[0]["user"]
    assert _CONTACT_EMAIL not in sent
    assert "[redacted-email]" in sent
    assert "the loop is done" in sent


def test_conn_less_path_redacts_too() -> None:
    client = _FakeClient(_VALID_ROUTINE_RESPONSE)
    route_followup(
        _app(), _history(notes=f"Contact {_CONTACT_EMAIL} about scheduling."), client=client
    )
    assert _CONTACT_EMAIL not in client.calls[0]["user"]


def test_route_followup_propagates_sanitizer_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sanitizer's refusal path (D7 M5, D8) must propagate rather than be
    swallowed -- same guard extraction/scoring already have."""
    import jscc.stage_call as stage_call

    original_sanitize = stage_call.sanitize_for_llm

    def _force_flag(payload, **kwargs):
        payload["contains_personal"] = True
        return original_sanitize(payload, **kwargs)

    monkeypatch.setattr(stage_call, "sanitize_for_llm", _force_flag)
    with pytest.raises(SanitizerRefusal):
        route_followup(_app(), _history(), client=StubRoutingClient())


@pytest.mark.parametrize(
    "answer",
    [
        {"classification": "routine", "intent": "cadence_nudge", "reason": "comp talk pending"},
        {"classification": "routine", "intent": "cadence_nudge", "considerations": ["salary"]},
        {"classification": "routine", "intent": "   "},
        {"classification": "routine"},
    ],
)
def test_a_hedged_or_intentless_routine_answer_is_a_parse_error(answer: dict) -> None:
    with pytest.raises(RoutingParseError):
        route_followup(_app(), _history(), client=_FakeClient(json.dumps(answer)))
