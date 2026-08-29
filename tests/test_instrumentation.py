from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jscc.instrumentation import LLMResult, instrumented
from jscc.storage import _connect, _init_db, list_llm_calls


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "test.db"
    c = _connect(db_path)
    _init_db(c)
    yield c
    c.close()


def test_instrumented_records_call_end_to_end(conn: sqlite3.Connection) -> None:
    calls_made = []

    @instrumented("extraction")
    def fake_llm_call(conn, model, prompt, extra=None):
        calls_made.append((model, prompt, extra))
        return LLMResult(
            output={"level": "senior"},
            input_tokens=120,
            output_tokens=40,
            cost_usd=0.0023,
        )

    output = fake_llm_call(conn, "claude-haiku", "extract this JD", extra="unused")

    assert output == {"level": "senior"}
    assert calls_made == [("claude-haiku", "extract this JD", "unused")]

    records = list_llm_calls(conn)
    assert len(records) == 1
    record = records[0]
    assert record.feature == "extraction"
    assert record.model == "claude-haiku"
    assert record.input_tokens == 120
    assert record.output_tokens == 40
    assert record.cost_usd == 0.0023
    assert record.latency_ms >= 0
    assert len(record.prompt_hash) == 64  # sha256 hex digest


def test_instrumented_hashes_prompt_not_stores_it(conn: sqlite3.Connection) -> None:
    @instrumented("extraction")
    def fake_llm_call(conn, model, prompt):
        return LLMResult(output=None, input_tokens=1, output_tokens=1, cost_usd=0.0)

    fake_llm_call(conn, "claude-haiku", "contains a real JD, not for storage")

    row = conn.execute("SELECT prompt_hash FROM llm_calls").fetchone()
    assert "contains a real JD" not in row["prompt_hash"]


def test_instrumented_records_multiple_calls_separately(conn: sqlite3.Connection) -> None:
    @instrumented("scoring")
    def fake_llm_call(conn, model, prompt):
        return LLMResult(output=None, input_tokens=5, output_tokens=5, cost_usd=0.01)

    fake_llm_call(conn, "claude-sonnet", "prompt one")
    fake_llm_call(conn, "claude-sonnet", "prompt two")

    records = list_llm_calls(conn)
    assert len(records) == 2
    assert records[0].id != records[1].id
