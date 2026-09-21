"""Follow-up routing -- D10 step 1 of the routing-first drafter architecture.

The call goes through `stage_call.call_stage`, which sanitizes and verifies
the payload before any client sees it.

No `ANTHROPIC_API_KEY` is configured for this project (same as extraction
and scoring), so `default_routing_client()` resolves to `StubRoutingClient`
by default. Per D10's own bias -- a false-routine auto-draft is a much
larger product failure than a false-non-routine briefing card -- the stub's
fixed answer is `non_routine`, not an arbitrary placeholder: an
unconfigured router that never auto-drafts is the honestly correct "safe
when uncertain" behavior, not just a stand-in for one. The prompt was
validated against real model output captured by hand through Claude.ai chat
(see evals/README.md).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from .json_utils import strip_code_fence
from .llm_client import ROUTING_MODEL, LLMClient, default_routing_client
from .models import Application, Contact, Interaction, RoutingDecision
from .stage_call import call_stage
from .storage import list_contacts

ROUTING_SYSTEM_PROMPT = """You are a follow-up routing classifier for a job search tracker. Given an application and its interaction history, decide whether drafting the next follow-up message is a ROUTINE task safe to auto-draft, or a NON_ROUTINE situation that needs a human's judgment. Return ONLY a JSON object — no prose, no markdown fences — matching this shape:

{
  "classification": "routine" or "non_routine",
  "intent": a short snake_case label for the routine situation (e.g. "post_interview_thank_you", "cadence_nudge", "logistics_confirmation", "recruiter_acknowledgment") — required (non-null) when classification is "routine"; must be null when "non_routine",
  "reason": one sentence naming why this needs a human — required (non-null) when classification is "non_routine"; must be null when "routine",
  "considerations": a short list of specific things the human should weigh before responding — required (at least one item) when classification is "non_routine"; must be an empty list when "routine"
}

ROUTINE means a low-stakes, well-understood situation where a templated response carries no real risk of saying the wrong thing. Concrete examples: a thank-you note after an interview, a gentle check-in on an application that has gone quiet past its expected cadence, confirming logistics (times, formats) that the other side has already proposed and that the candidate's own next action names (for example "Confirm the 4pm time works" — one proposal to accept, not a choice among several), or acknowledging a first cold outreach from a recruiter.

NON_ROUTINE means the content of the reply carries real relationship, financial, or judgment risk, OR you are not confident which bucket this falls into. Concrete examples: replying to any rejection, including a warm one that invites the candidate to stay in touch or mentions a future opening (a rejection is never a routine acknowledgment — how the candidate responds shapes whether that door stays open, and a candidate who wants to ask for feedback needs even more care), anything touching compensation or negotiation, accepting or declining an offer (even a clean decline with no ask attached still commits the candidate to a specific outcome and affects the relationship going forward), a first outreach to a warm personal contact (the relationship history matters and a generic message could damage it), a reply that would have to state a specific detail about the candidate that neither the history nor their next action supplies (for example dietary needs, their availability, or which one of several proposed time slots or options they choose when the history does not say which; a next action like "Reply with preferred time" or "Reply with availability" names a task, not the answer; likewise a next action that only names a topic, such as "Confirm attendance and lunch needs", is a task, not the answer, because the candidate's actual needs are still unstated. An answer counts as recorded only when the notes or next action actually state it, for example "Reply confirming Tuesday at 10am". So picking a slot the history never records is non-routine even though it feels like scheduling — never guess it, and name the missing detail in `reason`), two threads (e.g. a recruiter and a hiring manager) giving conflicting or ambiguous instructions, and any interaction history that contains a personal or sensitive disclosure about a specific individual (health, family, or similarly private circumstances) — drafting around a disclosure like that needs a human's judgment about tone and whether to reference it at all.

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


# Application fields withheld from the drafter's prompts. None of them bears on
# whether a follow-up is routine or on what it should say, and `source_raw` is
# third-party posting text: sending it would let a hostile posting argue the
# router toward "routine", the one direction the router must not be pushed. The
# keys stay, set to each field's empty default, so the prompt keeps its shape.
_WITHHELD_APPLICATION_FIELDS = ("source_raw", "source_url", "extracted_jd", "fit_rationale")


def application_for_prompt(app: Application) -> dict[str, Any]:
    data = app.model_dump(mode="json")
    for field in _WITHHELD_APPLICATION_FIELDS:
        data[field] = Application.model_fields[field].default
    return data


def name_roles_for(contacts: list[Contact]) -> dict[str, str]:
    """Known contact names mapped to their roles, for the sanitizer to substitute.

    Full names only. The substitution is a case-insensitive substring match, so
    adding bare first names would also rewrite ordinary words that contain them.
    """
    return {c.name: c.role.value for c in contacts if c.name.strip()}


def _build_user_payload(app: Application, history: list[Interaction]) -> dict[str, Any]:
    return {
        "application": application_for_prompt(app),
        "history": [interaction.model_dump(mode="json") for interaction in history],
    }


# Separate ledger features for production and eval traffic, as in extraction.
ROUTING_FEATURE = "routing"
ROUTING_EVAL_FEATURE = "routing_eval"


def route_followup(
    app: Application,
    history: list[Interaction],
    *,
    conn: sqlite3.Connection | None = None,
    client: LLMClient | None = None,
    feature: str = ROUTING_FEATURE,
    contacts: list[Contact] | None = None,
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

    if contacts is None:
        contacts = list_contacts(conn, app.id) if conn is not None else []
    response = call_stage(
        stage="routing",
        model=ROUTING_MODEL,
        system=ROUTING_SYSTEM_PROMPT,
        user=_build_user_payload(app, history),
        client=client,
        conn=conn,
        feature=feature,
        features=(ROUTING_FEATURE, ROUTING_EVAL_FEATURE),
        parse_error=RoutingParseError,
        name_roles=name_roles_for(contacts),
    )
    return _parse_response(response.text)
