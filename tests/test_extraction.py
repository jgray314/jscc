from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jscc.extraction import (
    EXTRACTION_MODEL,
    ExtractionParseError,
    extract_jd,
)
from jscc.llm_client import LLMResponse, StubExtractionClient
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
    {
        "title": "Senior Backend Engineer",
        "level": "senior",
        "comp_band": "$180,000-$220,000",
        "location": None,
        "remote_policy": "remote",
        "must_have_skills": ["Python", "PostgreSQL"],
        "responsibilities_summary": "Owns billing services.",
    }
)


@pytest.fixture()
def conn(tmp_path: Path):
    c = _connect(tmp_path / "test.db")
    _init_db(c)
    yield c
    c.close()


# ---- stub fallback (no API key) ------------------------------------------------

def test_extract_jd_uses_stub_by_default_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = extract_jd("some JD text")
    assert "StubExtractionClient" in result.responsibilities_summary


def test_extract_jd_accepts_explicit_client() -> None:
    result = extract_jd("some JD text", client=StubExtractionClient())
    assert "StubExtractionClient" in result.responsibilities_summary


# ---- request wiring --------------------------------------------------------------

def test_extract_jd_sends_raw_text_as_user_prompt() -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    extract_jd("this is the raw JD", client=fake)
    assert fake.calls[0]["user"] == "this is the raw JD"
    assert fake.calls[0]["model"] == EXTRACTION_MODEL
    assert "JSON" in fake.calls[0]["system"]


# ---- response parsing -------------------------------------------------------------

def test_extract_jd_parses_valid_response() -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    result = extract_jd("raw jd", client=fake)
    assert result.title == "Senior Backend Engineer"
    assert result.level == "senior"
    assert result.must_have_skills == ["Python", "PostgreSQL"]


def test_extract_jd_raises_on_invalid_json() -> None:
    fake = _FakeClient("not valid json at all")
    with pytest.raises(ExtractionParseError, match="not valid JSON"):
        extract_jd("raw jd", client=fake)


def test_extract_jd_raises_on_json_missing_required_field() -> None:
    fake = _FakeClient(json.dumps({"title": "SWE"}))  # missing level, responsibilities_summary
    with pytest.raises(ExtractionParseError):
        extract_jd("raw jd", client=fake)


# ---- instrumentation (D5) ---------------------------------------------------------

def test_extract_jd_records_llm_call_when_conn_provided(conn: sqlite3.Connection) -> None:
    fake = _FakeClient(_VALID_RESPONSE, input_tokens=42, output_tokens=17, cost_usd=0.0055)
    extract_jd("raw jd", conn=conn, client=fake)

    records = list_llm_calls(conn)
    assert len(records) == 1
    record = records[0]
    assert record.feature == "extraction"
    assert record.model == EXTRACTION_MODEL
    assert record.input_tokens == 42
    assert record.output_tokens == 17
    assert record.cost_usd == 0.0055


def test_extract_jd_without_conn_does_not_require_a_database() -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    result = extract_jd("raw jd", client=fake)  # no conn — must not raise
    assert result.title == "Senior Backend Engineer"


def test_extract_jd_prompt_hash_does_not_leak_raw_jd(conn: sqlite3.Connection) -> None:
    fake = _FakeClient(_VALID_RESPONSE)
    extract_jd("a raw JD containing sensitive-looking text", conn=conn, client=fake)
    row = conn.execute("SELECT prompt_hash FROM llm_calls").fetchone()
    assert "sensitive-looking" not in row["prompt_hash"]
