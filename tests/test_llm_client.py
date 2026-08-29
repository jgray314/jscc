from __future__ import annotations

import pytest

from jscc.llm_client import (
    EXTRACTION_MODEL,
    AnthropicClient,
    StubExtractionClient,
    default_client,
)


def test_stub_returns_fixed_placeholder() -> None:
    client = StubExtractionClient()
    response = client.complete(model=EXTRACTION_MODEL, system="sys", user="user")
    assert response.input_tokens == 0
    assert response.output_tokens == 0
    assert response.cost_usd == 0.0
    assert "StubExtractionClient" in response.text


def test_default_client_is_stub_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(default_client(), StubExtractionClient)


def test_default_client_is_anthropic_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-for-construction-only")
    client = default_client()
    assert isinstance(client, AnthropicClient)


def test_anthropic_client_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicClient()


def test_anthropic_client_accepts_explicit_key() -> None:
    # Construction only — no network call. Proves the explicit api_key= path
    # doesn't require the env var.
    client = AnthropicClient(api_key="sk-fake-for-construction-only")
    assert client is not None
