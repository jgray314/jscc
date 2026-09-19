"""Follow-up routing -- D10 step 1 of the routing-first drafter architecture.

Slice D2a: real prompt + client plumbing, mirroring B2a's and C2a's shape.
Every call routes through the full D7/D8 choke point before anything
touches the network: build a payload -> `sanitize_for_llm` -> `send_to_llm`
(raises `LLMSendError` if verification fails) -> only then hand the
verified dict to an `LLMClient`.

No `ANTHROPIC_API_KEY` is configured for this project (same as extraction
and scoring), so `default_routing_client()` resolves to `StubRoutingClient`
by default. Per D10's own bias -- a false-routine auto-draft is a much
larger product failure than a false-non-routine briefing card -- the stub's
fixed answer is `non_routine`, not an arbitrary placeholder: an
unconfigured router that never auto-drafts is the honestly correct "safe
when uncertain" behavior, not just a stand-in for one. D2b (manual capture
+ replay through Claude.ai chat, mirroring B2b/C2b) is what actually
validates this prompt against real model output.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from .instrumentation import LLMResult, instrumented
from .json_utils import strip_code_fence
from .llm_client import ROUTING_MODEL, LLMClient, default_routing_client
from .models import Application, Interaction, RoutingDecision
from .sanitizer import sanitize_for_llm, send_to_llm

ROUTING_SYSTEM_PROMPT = """You are a follow-up routing classifier for a job search tracker. Given an application and its interaction history, decide whether drafting the next follow-up message is a ROUTINE task safe to auto-draft, or a NON_ROUTINE situation that needs a human's judgment. Return ONLY a JSON object — no prose, no markdown fences — matching this shape:

{
  "classification": "routine" or "non_routine",
  "intent": a short snake_case label for the routine situation (e.g. "post_interview_thank_you", "cadence_nudge", "logistics_confirmation", "recruiter_acknowledgment") — required (non-null) when classification is "routine"; must be null when "non_routine",
  "reason": one sentence naming why this needs a human — required (non-null) when classification is "non_routine"; must be null when "routine",
  "considerations": a short list of specific things the human should weigh before responding — required (at least one item) when classification is "non_routine"; must be an empty list when "routine"
}

ROUTINE means a low-stakes, well-understood situation where a templated response carries no real risk of saying the wrong thing. Concrete examples: a thank-you note after an interview, a gentle check-in on an application that has gone quiet past its expected cadence, confirming logistics (times, formats) that the other side has already proposed, or acknowledging a first cold outreach from a recruiter.

NON_ROUTINE means the content of the reply carries real relationship, financial, or judgment risk, OR you are not confident which bucket this falls into. Concrete examples: replying to any rejection, including a warm one that invites the candidate to stay in touch or mentions a future opening (a rejection is never a routine acknowledgment — how the candidate responds shapes whether that door stays open, and a candidate who wants to ask for feedback needs even more care), anything touching compensation or negotiation, accepting or declining an offer (even a clean decline with no ask attached still commits the candidate to a specific outcome and affects the relationship going forward), a first outreach to a warm personal contact (the relationship history matters and a generic message could damage it), two threads (e.g. a recruiter and a hiring manager) giving conflicting or ambiguous instructions, and any interaction history that contains a personal or sensitive disclosure about a specific individual (health, family, or similarly private circumstances) — drafting around a disclosure like that needs a human's judgment about tone and whether to reference it at all.

Bias hard toward NON_ROUTINE. Auto-drafting a situation that actually needed a human is a much larger failure than surfacing a situation the person could have drafted themselves — if you are genuinely unsure which bucket a situation falls into, classify it NON_ROUTINE and say so honestly in `reason` (e.g. "ambiguous intent, no clear ask" is a valid reason). Judge ROUTINE vs. NON_ROUTINE on the actual stakes and clarity of the situation, not on how short the history is or how polite the language sounds.
"""


class RoutingParseError(ValueError):
    """The model's response wasn't valid JSON matching RoutingDecision's shape."""


def _parse_response(text: str) -> RoutingDecision:
    try:
        data: Any = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as e:
        raise RoutingParseError(f"routing response was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise RoutingParseError(
            f"routing response was valid JSON but not an object: {type(data).__name__}"
        )
    try:
        return RoutingDecision(**data)
    except (TypeError, ValidationError) as e:
        raise RoutingParseError(f"routing response did not match RoutingDecision: {e}") from e


def _build_user_prompt(app: Application, history: list[Interaction]) -> str:
    return json.dumps(
        {
            "application": app.model_dump(mode="json"),
            "history": [interaction.model_dump(mode="json") for interaction in history],
        },
        sort_keys=True,
    )


def _raw_routing_call(
    conn: sqlite3.Connection, model: str, prompt: str, *, client: LLMClient, system: str
) -> LLMResult:
    """The billed unit, and only the billed unit — same split as
    `extraction._raw_extraction_call` / `scoring._raw_scoring_call`: nothing
    that can fail after the money is spent belongs in here, so parsing lives
    in `route_followup`."""
    response = client.complete(model=model, system=system, user=prompt)
    return LLMResult(
        output=response,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
    )


# One pre-decorated variant per ledger feature, same reasoning as
# extraction.py's / scoring.py's `_CALL_BY_FEATURE`: separating production
# `route_followup` calls from `eval routing` traffic keeps prompt iteration
# off the per-application cost figure in `jscc costs`.
ROUTING_FEATURE = "routing"
ROUTING_EVAL_FEATURE = "routing_eval"

_CALL_BY_FEATURE = {
    ROUTING_FEATURE: instrumented(ROUTING_FEATURE)(_raw_routing_call),
    ROUTING_EVAL_FEATURE: instrumented(ROUTING_EVAL_FEATURE)(_raw_routing_call),
}


def route_followup(
    app: Application,
    history: list[Interaction],
    *,
    conn: sqlite3.Connection | None = None,
    client: LLMClient | None = None,
    feature: str = ROUTING_FEATURE,
) -> RoutingDecision:
    """Classify whether the next follow-up for `app` is safe to auto-draft.

    `conn`, when passed, records the call to the `llm_calls` ledger (D5) via
    `@instrumented`, under `feature`. The `route` CLI command supplies one as
    `routing`; `eval routing` as `routing_eval`.

    The conn-less path remains for library and test callers, same as
    `extract_jd`/`score_fit` — the honest scope of D5's claim is that calls
    made through a CLI command are instrumented, not every embedded caller.
    """
    client = client or default_routing_client()

    payload = {
        "model": ROUTING_MODEL,
        "system": ROUTING_SYSTEM_PROMPT,
        "user": _build_user_prompt(app, history),
        # Not flagged: an application's own metadata and its interaction
        # history describe the candidate's own job search, not a named third
        # party forwarded wholesale — same reasoning scoring.py's payload
        # uses. The sanitizer redacts every payload unconditionally
        # regardless of this flag (D7 M5) -- that guarantee is what protects
        # a contact's name inside an interaction note, not this comment.
        "contains_personal": False,
    }
    sanitized = sanitize_for_llm(payload)
    verified = send_to_llm(sanitized)

    if conn is not None:
        try:
            call = _CALL_BY_FEATURE[feature]
        except KeyError:
            raise ValueError(
                f"unknown instrumentation feature {feature!r}; "
                f"expected one of {sorted(_CALL_BY_FEATURE)}"
            ) from None
        response = call(
            conn, verified["model"], verified["user"], client=client, system=verified["system"]
        )
    else:
        response = client.complete(
            model=verified["model"], system=verified["system"], user=verified["user"]
        )
    if response.stop_reason == "max_tokens":
        # Same reasoning as extraction's / scoring's truncation check (gate
        # finding M2): the tokens were already spent, so this is a
        # parse-error message, not a retry -- raised after the ledger row
        # would have been written.
        raise RoutingParseError(
            f"routing response was truncated at the model's max_tokens limit "
            f"after {response.output_tokens} output tokens; the JSON is incomplete. "
            "Raise max_tokens on the client, or shorten what the prompt asks for."
        )
    return _parse_response(response.text)
