from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jscc.config import Profile
from jscc.llm_client import LLMResponse, StubScoringClient
from jscc.models import ExtractedJD
from jscc.scoring import SCORING_MODEL, ScoringParseError, score_fit
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


_VALID_RESPONSE = json.dumps({"score": 85, "rationale": "Strong match on role focus and comp."})


def _extracted(**overrides) -> ExtractedJD:
    fields = dict(
        title="Senior Engineering Manager",
        level="senior",
        comp_band="$320,000-$420,000",
        location=None,
        remote_policy="remote",
        must_have_skills=["distributed systems"],
        responsibilities_summary="Leads a platform team.",
    )
    fields.update(overrides)
    return ExtractedJD(**fields)


def _profile(**overrides) -> Profile:
    fields = dict(
        display_name="Sample Candidate",
        role_focus=["engineering manager"],
        level_target="L6-L7 equivalent",
        experience_years=12,
        comp_target={"min_usd": 300000, "max_usd": 500000},
        must_haves=["remote or hybrid"],
        deal_breakers=["on-call rotations >1 week/month"],
    )
    fields.update(overrides)
    return Profile(**fields)


@pytest.fixture()
def conn(tmp_path: Path):
    c = _connect(tmp_path / "test.db")
    _init_db(c)
    yield c
    c.close()


# ---- stub fallback (no API key) ------------------------------------------------


def test_score_fit_uses_stub_by_default_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = score_fit(_extracted(), "raw jd text", _profile())
    assert "StubScoringClient" in result.rationale


def test_score_fit_accepts_explicit_client() -> None:
    result = score_fit(_extracted(), "raw jd text", _profile(), client=StubScoringClient())
    assert "StubScoringClient" in result.rationale


# ---- request wiring --------------------------------------------------------------


def test_score_fit_sends_extracted_raw_and_profile_in_user_prompt() -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    score_fit(_extracted(), "this is the raw JD", _profile(), client=fake)
    sent = json.loads(fake.calls[0]["user"])
    assert sent["raw_jd_text"] == "this is the raw JD"
    assert sent["extracted_jd"]["title"] == "Senior Engineering Manager"
    assert sent["profile"]["display_name"] == "Sample Candidate"
    assert fake.calls[0]["model"] == SCORING_MODEL
    assert "JSON" in fake.calls[0]["system"]


# ---- response parsing -------------------------------------------------------------


def test_score_fit_parses_valid_response() -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    result = score_fit(_extracted(), "raw jd", _profile(), client=fake)
    assert result.score == 85
    assert "Strong match" in result.rationale


def test_score_fit_raises_on_invalid_json() -> None:
    fake = _FakeClient("not valid json at all")
    with pytest.raises(ScoringParseError, match="not valid JSON"):
        score_fit(_extracted(), "raw jd", _profile(), client=fake)


def test_score_fit_raises_on_json_missing_required_field() -> None:
    fake = _FakeClient(json.dumps({"score": 50}))  # missing rationale
    with pytest.raises(ScoringParseError):
        score_fit(_extracted(), "raw jd", _profile(), client=fake)


# ---- instrumentation (D5) ---------------------------------------------------------


def test_score_fit_records_llm_call_when_conn_provided(conn: sqlite3.Connection) -> None:
    fake = _FakeClient(_VALID_RESPONSE, input_tokens=42, output_tokens=17, cost_usd=0.0055)
    score_fit(_extracted(), "raw jd", _profile(), conn=conn, client=fake)

    records = list_llm_calls(conn)
    assert len(records) == 1
    record = records[0]
    assert record.feature == "scoring"
    assert record.model == SCORING_MODEL
    assert record.input_tokens == 42
    assert record.output_tokens == 17
    assert record.cost_usd == 0.0055


def test_score_fit_without_conn_does_not_require_a_database() -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    result = score_fit(_extracted(), "raw jd", _profile(), client=fake)  # no conn — must not raise
    assert result.score == 85


# ---- the ledger records billed calls that fail to parse (same shape as M2) ---


def test_unparseable_response_still_records_the_ledger_row(conn: sqlite3.Connection) -> None:
    client = _FakeClient("not json at all", input_tokens=1234, output_tokens=56, cost_usd=0.0042)
    with pytest.raises(ScoringParseError):
        score_fit(_extracted(), "raw jd text", _profile(), conn=conn, client=client)

    calls = list_llm_calls(conn)
    assert len(calls) == 1
    assert calls[0].input_tokens == 1234
    assert calls[0].output_tokens == 56
    assert calls[0].cost_usd == pytest.approx(0.0042)


def test_conn_less_parse_failure_still_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ScoringParseError):
        score_fit(_extracted(), "raw jd text", _profile(), client=_FakeClient("not json at all"))


# ---- redaction actually reaches the client (same C1-shaped gap as extraction) -


_JD_EMAIL = "dana.reyes" + "@" + "riftcloud.example"


def test_client_receives_redacted_text_not_the_raw_jd(conn: sqlite3.Connection) -> None:
    client = _FakeClient(_VALID_RESPONSE)
    raw = f"Staff Engineer at Rift Cloud. Questions to {_JD_EMAIL}."
    score_fit(_extracted(), raw, _profile(), conn=conn, client=client)

    sent = client.calls[0]["user"]
    assert _JD_EMAIL not in sent
    assert "[redacted-email]" in sent
    assert "Staff Engineer at Rift Cloud" in sent


def test_conn_less_path_redacts_too(conn: sqlite3.Connection) -> None:
    client = _FakeClient(_VALID_RESPONSE)
    score_fit(_extracted(), f"Contact {_JD_EMAIL} about the role.", _profile(), client=client)
    assert _JD_EMAIL not in client.calls[0]["user"]
