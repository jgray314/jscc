"""Follow-up composition -- D10 step 2A of the routing-first drafter architecture.

Slice D4a: real prompt + client plumbing, mirroring D2a's shape. Per D10,
this is a Sonnet call reached only when `route_followup` (D2) has already
classified a situation `routine` -- a `non_routine` situation never reaches
this function; it gets a briefing card (D5) instead. Getting composition
wrong (wrong tone, hallucinated facts, ignoring the declared intent) is a
real failure, but a strictly smaller one than D2's false-routine failure
mode: the drafter already refused to auto-draft anything a human needed to
handle.

Every call routes through the full D7/D8 choke point before anything touches
the network: build a payload -> `sanitize_for_llm` -> `send_to_llm` (raises
`LLMSendError` if verification fails) -> only then hand the verified dict to
an `LLMClient`. No `ANTHROPIC_API_KEY` is configured for this project, so
`default_composition_client()` resolves to `StubCompositionClient`; D4b
(manual capture through Claude.ai chat, mirroring B2b/C2b/D2b) is what
validates this prompt against real model output.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from .instrumentation import LLMResult, instrumented
from .json_utils import strip_code_fence
from .llm_client import COMPOSITION_MODEL, LLMClient, default_composition_client
from .models import Application, DraftEmail, Interaction
from .sanitizer import sanitize_for_llm, send_to_llm

COMPOSITION_SYSTEM_PROMPT = """You draft a short follow-up email on behalf of a job candidate, in the candidate's own voice. You are given a JSON object with four fields: "application" (the role and company), "history" (the candidate's interaction history for that application, oldest first), "intent" (a snake_case label naming the purpose of this email, e.g. "post_interview_thank_you", "cadence_nudge", "logistics_confirmation"), and "style_samples" (1 to 3 short passages the candidate actually wrote). Return ONLY a JSON object — no prose, no markdown fences — matching this shape:

{
  "subject": a short, specific subject line of 8 words or fewer,
  "body": the email body as plain text, with paragraphs separated by blank lines
}

Missing information. Escalate only when the reply cannot honestly be written without a specific fact the input does not supply, for example the candidate's dietary needs, which of several proposed times they prefer, or availability they have not stated. Do not guess. Return ONLY this instead, naming the missing detail in one short sentence:

{
  "needs_input": "the detail the candidate must supply"
}

Do not escalate just because the email could say more. If the candidate's next action already records the decision (for example "Confirm the time" or "Reply confirming Tuesday at 10am"), treat that as the answer and write the reply. If a warm, general reply that states nothing unrecorded would serve (thanking them, saying you will pick a slot or find a time), write that instead.

Write the email so the candidate could paste it straight into an email client.

Facts. Use only what the application and history actually say. Never invent a detail: no interviewer names, topics discussed, dates, times, numbers, commitments, or outcomes that are not in the input. When the history names something specific about the last touchpoint (a topic, a format, a proposed time), refer to it concretely. When the history is thin, write a shorter, more general email instead of padding it with guesses.

Purpose. Let the intent decide what the email is for and keep it to that one purpose: a thank-you expresses specific gratitude and enthusiasm, a cadence nudge is a brief, friendly check-in that restates interest without pressure, a logistics confirmation confirms exactly what the other side proposed and answers what they asked. Do not raise compensation, other offers, or anything the intent and history do not call for, and do not propose new times, dates, or terms that the history does not contain.

Voice. Match the register of the style samples: sentence length, warmth, contractions, and level of formality. Echo their voice, not their exact wording, and never copy a sample sentence verbatim. Keep it short, roughly 50 to 130 words, and natural rather than templated.

Format. Do not sign with a name and do not use a bracketed placeholder for one; end with a plain closing such as "Best,". If the input contains a bracketed redaction token such as "[redacted-email]", treat it as an unknown and never reproduce it in the email. No bracketed placeholders anywhere in the subject or body.
"""


class CompositionParseError(ValueError):
    """The model's response wasn't valid JSON matching DraftEmail's shape."""


def _parse_response(text: str) -> DraftEmail:
    try:
        data: Any = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as e:
        raise CompositionParseError(f"composition response was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise CompositionParseError(
            f"composition response was valid JSON but not an object: {type(data).__name__}"
        )
    needs_input = data.get("needs_input")
    if needs_input is not None and not isinstance(needs_input, str):
        raise CompositionParseError(
            f"composition response had a non-string needs_input: {type(needs_input).__name__}"
        )
    if needs_input and needs_input.strip():
        # Wins over any draft the model also returned: a draft written next to
        # "I don't know X" is a draft built on a guess.
        return DraftEmail(needs_input=needs_input.strip())
    missing = [k for k in ("subject", "body") if k not in data]
    if missing:
        raise CompositionParseError(
            f"composition response did not match DraftEmail: missing {', '.join(missing)}"
        )
    try:
        return DraftEmail(subject=data["subject"], body=data["body"])
    except (TypeError, ValidationError) as e:
        raise CompositionParseError(f"composition response did not match DraftEmail: {e}") from e


def _build_user_prompt(
    app: Application, history: list[Interaction], intent: str, style_samples: list[str]
) -> str:
    return json.dumps(
        {
            "application": app.model_dump(mode="json"),
            "history": [interaction.model_dump(mode="json") for interaction in history],
            "intent": intent,
            "style_samples": style_samples,
        },
        sort_keys=True,
    )


def _raw_composition_call(
    conn: sqlite3.Connection, model: str, prompt: str, *, client: LLMClient, system: str
) -> LLMResult:
    """The billed unit, and only the billed unit -- same split as
    `routing._raw_routing_call`: nothing that can fail after the money is
    spent belongs in here, so parsing lives in `compose_followup`."""
    response = client.complete(model=model, system=system, user=prompt)
    return LLMResult(
        output=response,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
    )


# One pre-decorated variant per ledger feature, same reasoning as routing.py's
# `_CALL_BY_FEATURE`: keeping `eval composition` traffic off the production
# figure in `jscc costs` means prompt iteration doesn't inflate per-application
# cost.
COMPOSITION_FEATURE = "composition"
COMPOSITION_EVAL_FEATURE = "composition_eval"

_CALL_BY_FEATURE = {
    COMPOSITION_FEATURE: instrumented(COMPOSITION_FEATURE)(_raw_composition_call),
    COMPOSITION_EVAL_FEATURE: instrumented(COMPOSITION_EVAL_FEATURE)(_raw_composition_call),
}


def compose_followup(
    app: Application,
    history: list[Interaction],
    intent: str,
    style_samples: list[str],
    *,
    conn: sqlite3.Connection | None = None,
    client: LLMClient | None = None,
    feature: str = COMPOSITION_FEATURE,
) -> DraftEmail:
    """Draft the follow-up email for a situation the router called routine.

    `conn`, when passed, records the call to the `llm_calls` ledger (D5) via
    `@instrumented`, under `feature`. The conn-less path remains for library
    and test callers, same as `route_followup`.
    """
    client = client or default_composition_client()

    payload = {
        "model": COMPOSITION_MODEL,
        "system": COMPOSITION_SYSTEM_PROMPT,
        "user": _build_user_prompt(app, history, intent, style_samples),
        # Not flagged: the application, its history, and the candidate's own
        # style samples describe the candidate's own job search, not a named
        # third party forwarded wholesale -- same reasoning routing.py's
        # payload uses. The sanitizer redacts every payload unconditionally
        # regardless of this flag (D7 M5); that guarantee, not this comment,
        # is what protects a contact's email inside a note or a sample.
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
        # Same reasoning as routing's truncation check (gate finding M2): the
        # tokens were already spent, so this is a parse-error message, not a
        # retry -- raised after the ledger row has been written.
        raise CompositionParseError(
            f"composition response was truncated at the model's max_tokens limit "
            f"after {response.output_tokens} output tokens; the JSON is incomplete. "
            "Raise max_tokens on the client, or shorten what the prompt asks for."
        )
    return _parse_response(response.text)
