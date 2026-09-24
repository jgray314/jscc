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

# Per D9: extraction is structured (Haiku territory), scoring is judgment
# (Sonnet territory). Moved from the dated Sonnet 4.5 id to Sonnet 5 on
# 2026-09-24: the manual capture rounds run in Claude.ai chat, whose default
# Sonnet has been Sonnet 5 since its 2026-06-30 launch, so the recordings were
# never Sonnet 4.5 and the id has to match what produced them. Sonnet 5 has no
# date suffix, so no split literal is needed here.
SCORING_MODEL = "claude-sonnet-5"

# Per D10: routing is a classification, not a draft -- same "structured,
# cheap" shape as extraction, so it reuses EXTRACTION_MODEL rather than
# introducing a second Haiku model id with its own rate entry to keep in
# sync. An alias, not a coincidence: if extraction's model ever changes,
# routing changes with it deliberately (same call site update), not by
# silently drifting out of sync the way two independently-hand-copied
# constants would.
ROUTING_MODEL = EXTRACTION_MODEL

# Per D10 step 2A: composition writes in the candidate's voice, which is a
# judgment call, so it sits on the scoring tier (Sonnet). Aliased to
# SCORING_MODEL for the same reason ROUTING_MODEL aliases EXTRACTION_MODEL:
# one model id and one rate entry to keep in sync, not two hand-copied ones.
COMPOSITION_MODEL = SCORING_MODEL

# Published rates, verified 2026-09-05 (Sonnet 5 entry: 2026-09-24) against
# https://platform.claude.com/docs/en/about-claude/pricing
#
# These are a hand-copied constant, not a runtime lookup, so they go stale
# silently -- and they had: the figures here were 20% under the published ones
# until this check, which meant the ledger under-reported every call while
# looking exactly as authoritative as a correct one. `rates_for` refuses an
# unknown model precisely so cost is never invented; a wrong rate for a *known*
# model slips past that guard entirely, because nothing is missing. Re-verify
# against the link above whenever the model changes or a bill looks off.
_MODEL_RATES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    EXTRACTION_MODEL: (1.00, 5.00),  # (input, output) USD per million tokens
    # Sonnet 5's launch pricing ($2/$10) became the standard price; the
    # scheduled 2026-09-01 increase to $3/$15 was cancelled (pricing page,
    # footnote 3, checked 2026-09-24).
    SCORING_MODEL: (2.00, 10.00),
}


class UnknownModelPricingError(RuntimeError):
    """No published rate is on file for this model, so its cost can't be recorded."""


def rates_for(model: str) -> tuple[float, float]:
    """(input, output) USD per million tokens, or raise.

    Raises rather than falling back to a default rate: pricing an unknown
    model at a known one under-reports spend by roughly an order of magnitude,
    silently, in the one artifact whose stated purpose is cost transparency.
    A ledger that quietly invents a number is worse than one that refuses.

    The check runs *before* the request is sent (see `complete`), not while
    pricing the response. Raising afterwards would spend the tokens and then
    throw away the record, the same failure that keeps parsing outside the
    metered call.
    """
    try:
        return _MODEL_RATES_USD_PER_MTOK[model]
    except KeyError:
        raise UnknownModelPricingError(
            f"no cost rate on file for model {model!r}; add it to "
            "_MODEL_RATES_USD_PER_MTOK in jscc/llm_client.py (rates: "
            "https://www.anthropic.com/pricing) rather than letting the "
            "ledger record a wrong figure. Known models: "
            f"{sorted(_MODEL_RATES_USD_PER_MTOK)}"
        ) from None


_STUB_RESPONSE_TEXT = """{
  "title": "",
  "company": null,
  "level": "unspecified",
  "comp_band": null,
  "location": null,
  "remote_policy": null,
  "must_have_skills": [],
  "responsibilities_summary": "no live extraction -- ANTHROPIC_API_KEY is not configured; this is a placeholder response from StubExtractionClient (jscc/llm_client.py)."
}"""

_STUB_SCORING_RESPONSE_TEXT = """{
  "score": 0,
  "rationale": "no live scoring -- ANTHROPIC_API_KEY is not configured; this is a placeholder response from StubScoringClient (jscc/llm_client.py)."
}"""

# Fixed at "non_routine", not an arbitrary placeholder like the other two
# stubs -- per D10's own bias (a false-routine auto-draft is a much larger
# failure than a false-non-routine briefing card), a router that always
# refuses to auto-draft when it has no real model behind it is the honestly
# correct "safe when uncertain" behavior, not just a stand-in.
_STUB_ROUTING_RESPONSE_TEXT = """{
  "classification": "non_routine",
  "intent": null,
  "reason": "no live routing -- ANTHROPIC_API_KEY is not configured; this is a placeholder response from StubRoutingClient (jscc/llm_client.py).",
  "considerations": ["no live model call was made; defaulting to non_routine per D10's false-routine bias"]
}"""


class LLMResponse(BaseModel):
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    # Why the caller needs this: a response cut off at max_tokens is truncated
    # JSON, which fails to parse for a reason that has nothing to do with the
    # prompt. Without it, "the model wrote bad JSON" and "we didn't give the
    # model room to finish" are the same error message during prompt
    # iteration.
    stop_reason: str | None = None


class LLMClient(Protocol):
    # Takes three bare strings, so the type system does not stop a caller from
    # skipping the sanitizer. `stage_call.call_stage` is the only caller, and
    # tests/test_llm_egress.py fails if any other module calls this. ADR-005
    # records why that is enforced by test rather than by an authenticated type.
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
        # Priced before the call, so an unknown model costs nothing to find
        # out about.
        input_rate, output_rate = rates_for(model)
        response = self._client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        cost_usd = (
            response.usage.input_tokens / 1_000_000 * input_rate
            + response.usage.output_tokens / 1_000_000 * output_rate
        )
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=cost_usd,
            stop_reason=getattr(response, "stop_reason", None),
        )


class StubExtractionClient:
    """No API key configured. See module docstring."""

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text=_STUB_RESPONSE_TEXT,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            stop_reason="end_turn",
        )


class StubScoringClient:
    """No API key configured. Scoring's counterpart to `StubExtractionClient`
    — a fixed, clearly-labeled placeholder so `score_fit`'s call path, the
    sanitizer routing, and the eval harness are exercisable end-to-end
    without a key or any spend. Eval pass rate against it is expected to be
    near zero, same as extraction's."""

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text=_STUB_SCORING_RESPONSE_TEXT,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            stop_reason="end_turn",
        )


# Empty subject on purpose: `grade_composition` checks a non-empty subject, so
# an unconfigured run reads as failing rather than as a suite of passing drafts.
_STUB_COMPOSITION_RESPONSE_TEXT = """{
  "subject": "",
  "body": "no live composition -- ANTHROPIC_API_KEY is not configured; this is a placeholder response from StubCompositionClient (jscc/llm_client.py)."
}"""


class StubRoutingClient:
    """No API key configured. Routing's counterpart to `StubExtractionClient`
    / `StubScoringClient` -- a fixed, clearly-labeled placeholder so
    `route_followup`'s call path, the sanitizer routing, and the eval
    harness are exercisable end-to-end without a key or any spend. Unlike
    the other two stubs, its fixed answer is a deliberate one (see
    `_STUB_ROUTING_RESPONSE_TEXT`), not an arbitrary zero/empty value."""

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text=_STUB_ROUTING_RESPONSE_TEXT,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            stop_reason="end_turn",
        )


class StubCompositionClient:
    """No API key configured. Composition's counterpart to the other stubs: a
    fixed, clearly-labeled placeholder so `compose_followup`'s call path, the
    sanitizer routing, and the eval harness are exercisable end-to-end without
    a key or any spend. Eval pass rate against it is expected to be zero."""

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text=_STUB_COMPOSITION_RESPONSE_TEXT,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            stop_reason="end_turn",
        )


def default_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    return StubExtractionClient()


def default_scoring_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    return StubScoringClient()


def default_routing_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    return StubRoutingClient()


def default_composition_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    return StubCompositionClient()


# The placeholder clients `default_*_client()` falls back to without a key. Their
# output is fixed text, never model output, so nothing may record it.
STUB_CLIENTS = (
    StubExtractionClient,
    StubScoringClient,
    StubRoutingClient,
    StubCompositionClient,
)
