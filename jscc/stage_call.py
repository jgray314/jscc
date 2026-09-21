"""The one path from an LLM stage to the model client.

Extraction, scoring, routing and composition each build a prompt and parse a
response; everything between those two steps is the same and lives here:
sanitize -> verify -> (optionally) record to the ledger -> call the client ->
refuse a truncated response. Only this module calls `LLMClient.complete`, so the
guarantee that every model call went through the sanitizer is a property of the
call graph rather than of four copies agreeing with each other.

`user` may be a plain string (extraction's raw posting text) or a JSON-native
dict (the other three stages). A dict is redacted value by value and serialized
only after the send boundary has verified it. Serializing first and redacting
the resulting string had two failure modes: `json.dumps` escapes non-ASCII to
`\\uXXXX`, so a danger-list name with a diacritic never matched its escaped
form, and a redaction pattern could consume JSON syntax (a quote) and hand the
model invalid JSON.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from functools import cache
from typing import Any

from .instrumentation import LLMResult, instrumented
from .llm_client import LLMClient, LLMResponse
from .sanitizer import sanitize_for_llm, send_to_llm


def serialize_user(user: str | dict[str, Any]) -> str:
    """The exact prompt text sent for `user`, and the text recordings are keyed on.

    `sort_keys` and the default ASCII escaping are what every recording in
    `evals/*/recorded.json` was captured against; changing either changes every
    key and invalidates the recordings.
    """
    if isinstance(user, str):
        return user
    return json.dumps(user, sort_keys=True)


def _raw_call(
    conn: sqlite3.Connection, model: str, prompt: str, *, client: LLMClient, system: str
) -> LLMResult:
    """The billed unit, and only the billed unit: nothing that can fail after the
    money is spent belongs in here, so parsing stays with each stage."""
    response = client.complete(model=model, system=system, user=prompt)
    return LLMResult(
        output=response,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
    )


@cache
def _instrumented_call(feature: str) -> Callable[..., LLMResponse]:
    return instrumented(feature)(_raw_call)


def call_stage(
    *,
    stage: str,
    model: str,
    system: str,
    user: str | dict[str, Any],
    client: LLMClient,
    conn: sqlite3.Connection | None,
    feature: str,
    features: Iterable[str],
    parse_error: type[Exception],
    name_roles: Mapping[str, str] | None = None,
) -> LLMResponse:
    """Send one stage's prompt through the sanitizer and return the model's response.

    `conn`, when passed, records the call to the `llm_calls` ledger under
    `feature`, which must be one of the stage's own `features` (its production
    and eval ledger names, kept apart so prompt iteration stays off the
    per-application cost figure). Without `conn` the call is made unrecorded;
    that path exists for library and test callers.

    `name_roles` maps known contact names to roles, so a name in a note goes out
    as `[contact:<role>]`.

    Raises `parse_error` when the response was cut off at `max_tokens`: the tokens
    are already spent and the ledger row already written, so this is a message to
    the caller, not a retry.
    """
    payload = {
        "model": model,
        "system": system,
        "user": user,
        # Not flagged by any stage: a posting, a profile and an application's own
        # history describe the candidate's own search, not a third party forwarded
        # wholesale. This flag is not what protects the call. Redaction runs on
        # every payload regardless of it, and no caller can opt out.
        "contains_personal": False,
    }
    verified = send_to_llm(sanitize_for_llm(payload, name_roles=name_roles))
    prompt = serialize_user(verified["user"])

    if conn is not None:
        if feature not in set(features):
            raise ValueError(
                f"unknown instrumentation feature {feature!r} for {stage}; "
                f"expected one of {sorted(features)}"
            )
        response = _instrumented_call(feature)(
            conn, verified["model"], prompt, client=client, system=verified["system"]
        )
    else:
        response = client.complete(model=verified["model"], system=verified["system"], user=prompt)

    if response.stop_reason == "max_tokens":
        raise parse_error(
            f"{stage} response was truncated at the model's max_tokens limit "
            f"after {response.output_tokens} output tokens; the JSON is incomplete. "
            "Raise max_tokens on the client, or shorten what the prompt asks for."
        )
    return response
