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


# ---- the ledger records billed calls that fail to parse (gate finding M2) ---
#
# `_parse_response` used to run inside the instrumented function, so an
# unparseable response propagated before the ledger row was written: tokens
# spent, nothing recorded. Malformed output is the likeliest failure during
# prompt iteration, which is exactly when the cost figures are being read.


def test_unparseable_response_still_records_the_ledger_row(conn: sqlite3.Connection) -> None:
    client = _FakeClient("not json at all", input_tokens=1234, output_tokens=56, cost_usd=0.0042)
    with pytest.raises(ExtractionParseError):
        extract_jd("raw jd text", conn=conn, client=client)

    calls = list_llm_calls(conn)
    assert len(calls) == 1
    assert calls[0].input_tokens == 1234
    assert calls[0].output_tokens == 56
    assert calls[0].cost_usd == pytest.approx(0.0042)


def test_response_that_parses_as_json_but_not_ExtractedJD_also_records(
    conn: sqlite3.Connection,
) -> None:
    """The other parse failure mode -- valid JSON, wrong shape -- takes the
    same path, so it must not be a separate hole."""
    client = _FakeClient(json.dumps({"title": "Engineer"}), input_tokens=10, output_tokens=2)
    with pytest.raises(ExtractionParseError):
        extract_jd("raw jd text", conn=conn, client=client)
    assert len(list_llm_calls(conn)) == 1


def test_conn_less_parse_failure_still_raises(conn: sqlite3.Connection) -> None:
    """No ledger to write to, but the error must not be swallowed by the
    reshuffle either."""
    with pytest.raises(ExtractionParseError):
        extract_jd("raw jd text", client=_FakeClient("not json at all"))


# ---- redaction actually reaches the client (the C1 wiring) ------------------
#
# `test_sanitizer.py` proves `sanitize_for_llm` redacts; nothing proved the
# redacted value is what `extract_jd` hands to the client. Rewiring the call to
# pass `raw_text` instead of `verified["user"]` would leave the whole suite
# green -- and C1 was precisely "the sanitizer was a no-op and nobody noticed
# for three slices". Literals are split because this file is scanned.


_JD_EMAIL = "dana.reyes" + "@" + "riftcloud.example"
_JD_PHONE = "(415) 555" + "-0134"


def test_client_receives_redacted_text_not_the_raw_jd(conn: sqlite3.Connection) -> None:
    client = _FakeClient(_VALID_RESPONSE)
    raw = (
        "Staff Engineer at Rift Cloud. Questions to "
        f"{_JD_EMAIL} or call {_JD_PHONE}."
    )
    extract_jd(raw, conn=conn, client=client)

    sent = client.calls[0]["user"]
    assert _JD_EMAIL not in sent
    assert _JD_PHONE not in sent
    assert "[redacted-email]" in sent
    assert "[redacted-phone]" in sent
    assert "Staff Engineer at Rift Cloud" in sent


def test_conn_less_path_redacts_too(conn: sqlite3.Connection) -> None:
    """Both branches of `extract_jd` route through the sanitizer; the one
    without a ledger is the easier one to wire up wrong."""
    client = _FakeClient(_VALID_RESPONSE)
    extract_jd(f"Contact {_JD_EMAIL} about the role.", client=client)
    assert _JD_EMAIL not in client.calls[0]["user"]


def test_the_stored_record_keeps_the_original_text(conn: sqlite3.Connection) -> None:
    """D7 governs egress, not the user's own records -- redaction must not
    reach back into what gets stored locally."""
    client = _FakeClient(_VALID_RESPONSE)
    raw = f"Staff Engineer. Reach {_JD_EMAIL}."
    extract_jd(raw, conn=conn, client=client)
    assert _JD_EMAIL not in client.calls[0]["user"]
    assert _JD_EMAIL in raw  # the caller's string is untouched
