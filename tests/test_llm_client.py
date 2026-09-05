from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jscc.llm_client import (
    EXTRACTION_MODEL,
    AnthropicClient,
    StubExtractionClient,
    UnknownModelPricingError,
    default_client,
    rates_for,
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


# ---- cost rates (gate finding M4) ------------------------------------------
#
# An unknown model used to be priced at the default (Haiku) rate, so a swap to
# Sonnet or Opus would under-report spend by roughly 10x with no signal -- in
# the one artifact whose whole point is cost transparency.


def test_rates_for_known_model() -> None:
    assert rates_for(EXTRACTION_MODEL) == (0.80, 4.00)


def test_rates_for_unknown_model_raises_and_names_the_fix() -> None:
    with pytest.raises(UnknownModelPricingError) as excinfo:
        rates_for("claude-some-model-nobody-priced")
    assert "claude-some-model-nobody-priced" in str(excinfo.value)
    assert "_MODEL_RATES_USD_PER_MTOK" in str(excinfo.value)


def _fake_sdk(input_tokens: int, output_tokens: int) -> Mock:
    """Stands in for `anthropic.Anthropic` -- one text block and a usage record."""
    sdk = Mock()
    sdk.messages.create.return_value = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="hello"),
            SimpleNamespace(type="thinking", text="ignored"),
        ],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )
    return sdk


def test_unknown_model_is_rejected_before_the_request_is_sent() -> None:
    """The check has to run first. Raising while pricing the response would
    spend the tokens and then discard the record -- the M2 failure again."""
    client = AnthropicClient(api_key="sk-fake-for-construction-only")
    sdk = _fake_sdk(100, 50)
    client._client = sdk
    with pytest.raises(UnknownModelPricingError):
        client.complete(model="claude-some-model-nobody-priced", system="sys", user="user")
    sdk.messages.create.assert_not_called()


def test_complete_returns_text_blocks_and_the_priced_cost() -> None:
    """First coverage of the real `complete()` body: block filtering and the
    cost arithmetic had never executed (gate finding L1, partially)."""
    client = AnthropicClient(api_key="sk-fake-for-construction-only")
    client._client = _fake_sdk(1_000_000, 1_000_000)
    response = client.complete(model=EXTRACTION_MODEL, system="sys", user="user")
    assert response.text == "hello"  # the non-text block is dropped
    assert response.input_tokens == 1_000_000
    assert response.output_tokens == 1_000_000
    assert response.cost_usd == pytest.approx(0.80 + 4.00)
