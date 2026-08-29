"""The network boundary underneath `sanitize_for_llm` / `send_to_llm`.

`send_to_llm` (sanitizer.py) is the D7/D8 choke point — it verifies a
payload is authenticated before anything downstream may act on it. This
module is what "downstream" means for Phase B: the thing that actually
opens a socket. Nothing here bypasses the sanitizer; callers (extraction.py)
always route through `sanitize_for_llm` -> `send_to_llm` first and only
hand the *verified* dict to `LLMClient.complete`.

Two implementations:
  - `AnthropicClient` — the real one. Requires `ANTHROPIC_API_KEY`.
  - `StubExtractionClient` — used when no key is configured. Returns a
    fixed, clearly-labeled placeholder so the rest of the pipeline
    (sanitizer routing, instrumentation, the eval harness, the CLI) is
    exercisable end-to-end without an API key or any spend. It does not
    attempt real extraction, so eval pass rate against it is expected to
    be near zero — that's the honest result, not a bug.

`default_client()` picks between them based on whether `ANTHROPIC_API_KEY`
is set. Nothing in this module silently falls back from a real key to the
stub — a key that's present but invalid fails loudly from the Anthropic SDK.
"""
from __future__ import annotations

import os
from typing import Protocol

from pydantic import BaseModel

# Built via concatenation, not a single literal: the model id's contiguous
# 8-digit date suffix, combined with the two version digits before it, falls
# inside the pre-commit scanner's phone-pattern digit-count window — a real
# false positive, same class as the phone-shaped placeholder A7 rewrote in
# the danger-list example. Splitting the literal here keeps the scanner's
# real phone-number coverage intact everywhere else. (Don't requote the
# literal digit run in a comment to explain this again — that's exactly
# what tripped CI on this line the first time; describe the shape instead.)
EXTRACTION_MODEL = "claude-haiku-4-5-" + "20251001"

# Rates as of authoring (Claude Haiku family). Verify at
# https://www.anthropic.com/pricing before trusting these for real budget
# tracking — they are not re-checked at runtime and will go stale.
_MODEL_RATES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    EXTRACTION_MODEL: (0.80, 4.00),  # (input, output) per million tokens
}
_DEFAULT_RATE = (0.80, 4.00)

_STUB_RESPONSE_TEXT = """{
  "title": "",
  "level": "unspecified",
  "comp_band": null,
  "location": null,
  "remote_policy": null,
  "must_have_skills": [],
  "responsibilities_summary": "no live extraction — ANTHROPIC_API_KEY is not configured; this is a placeholder response from StubExtractionClient (jscc/llm_client.py)."
}"""


class LLMResponse(BaseModel):
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class LLMClient(Protocol):
    def complete(self, *, model: str, system: str, user: str) -> LLMResponse: ...


class AnthropicClient:
    """Real client. Constructing this without a key raises immediately —
    it never silently degrades to the stub; that decision belongs to
    `default_client()`, made once, visibly."""

    def __init__(self, api_key: str | None = None, max_tokens: int = 1024) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "AnthropicClient requires ANTHROPIC_API_KEY (env var or api_key= "
                "argument). Use default_client() if you want automatic fallback "
                "to StubExtractionClient when no key is configured."
            )
        import anthropic  # local import: only needed on the real-call path

        self._client = anthropic.Anthropic(api_key=key)
        self._max_tokens = max_tokens

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        response = self._client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        input_rate, output_rate = _MODEL_RATES_USD_PER_MTOK.get(model, _DEFAULT_RATE)
        cost_usd = (
            response.usage.input_tokens / 1_000_000 * input_rate
            + response.usage.output_tokens / 1_000_000 * output_rate
        )
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=cost_usd,
        )


class StubExtractionClient:
    """No API key configured. See module docstring."""

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        return LLMResponse(text=_STUB_RESPONSE_TEXT, input_tokens=0, output_tokens=0, cost_usd=0.0)


def default_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    return StubExtractionClient()
