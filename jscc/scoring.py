"""Fit scoring — D9 step 2 of the split extract/score architecture.

Slice C2a: real prompt + client plumbing, mirroring B2a's shape. Every call
routes through the full D7/D8 choke point before anything touches the
network: build a payload -> `sanitize_for_llm` -> `send_to_llm` (raises
`LLMSendError` if verification fails) -> only then hand the verified dict to
an `LLMClient`.

No `ANTHROPIC_API_KEY` is configured for this project (same as extraction),
so `default_scoring_client()` resolves to `StubScoringClient` by default and
`python -m jscc eval fit_scoring` reports a near-zero pass rate against it —
that's expected, not a regression. C2b (manual capture + replay through
Claude.ai chat, mirroring B2b) is what actually validates this prompt against
real model output.

Per D9, the scorer sees both the extracted structured JD AND the raw JD text
-- extraction inevitably loses signal (nuanced language that doesn't fit
clean structured fields but matters for fit judgment), and the augmented
input is a locked decision, not a future refinement.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from .config import Profile
from .instrumentation import LLMResult, instrumented
from .llm_client import SCORING_MODEL, LLMClient, default_scoring_client
from .models import ExtractedJD, FitResult
from .sanitizer import sanitize_for_llm, send_to_llm

SCORING_SYSTEM_PROMPT = """You are a job-fit scorer. Given a candidate's profile and a job posting (structured extraction plus the raw text), return ONLY a JSON object — no prose, no markdown fences — matching this shape:

{
  "score": number from 0 to 100,
  "rationale": a 1-3 sentence explanation naming the specific factors that drove the score
}

Weigh these factors, in this order of importance:

1. **Deal-breakers.** If the posting clearly matches any entry in the profile's `deal_breakers` (e.g. an on-call rotation heavier than what's listed as unacceptable), the score must land below 20 regardless of how well everything else matches. A deal-breaker is disqualifying, not a deduction.
2. **Role focus and level.** Compare the posting's title/level against the profile's `role_focus` and `level_target`. A posting for a role or level far outside the profile's focus (e.g. an individual-contributor-junior posting against a profile targeting engineering-manager/staff-plus roles) scores low even with a good comp match — level/role fit is not fungible with comp.
3. **Comp target.** Compare the posting's `comp_band` against the profile's `comp_target` range. Below-range comp caps the score in the middle band even if everything else matches well; comp above the profile's range is not a downside — score it as if it met the top of the range. A posting with no stated comp band is neither a bonus nor a penalty on this factor alone.
4. **Must-haves.** Check the posting (both the structured skills list and the raw text — a must-have like "remote or hybrid" is about `remote_policy`, not `must_have_skills`) against every entry in the profile's `must_haves`. Missing one must-have caps the score in the low-to-middle band; missing several pushes it toward the bottom.
5. **Skill overlap.** Only after the above: does the posting's `must_have_skills` list overlap with what the profile's role focus implies. This is the least weighted factor — a strong match on 1-4 with a thin skills list still scores well.

Use the raw JD text for nuance the structured extraction may have lost (tone, unstated implications, context around a listed requirement) — do not rely on the structured fields alone.

Always return a non-empty `rationale` naming the specific factor(s) that drove the score, even for a clean high-fit case ("meets all must-haves, comp and level align" is a valid rationale for a high score).
"""


class ScoringParseError(ValueError):
    """The model's response wasn't valid JSON matching FitResult's shape."""


def _parse_response(text: str) -> FitResult:
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as e:
        raise ScoringParseError(f"scoring response was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ScoringParseError(
            f"scoring response was valid JSON but not an object: {type(data).__name__}"
        )
    try:
        return FitResult(**data)
    except (TypeError, ValidationError) as e:
        raise ScoringParseError(f"scoring response did not match FitResult: {e}") from e


def _build_user_prompt(extracted: ExtractedJD, raw_jd_text: str, profile: Profile) -> str:
    return json.dumps(
        {
            "profile": profile.model_dump(),
            "extracted_jd": extracted.model_dump(),
            "raw_jd_text": raw_jd_text,
        },
        sort_keys=True,
    )


def _raw_scoring_call(
    conn: sqlite3.Connection, model: str, prompt: str, *, client: LLMClient, system: str
) -> LLMResult:
    """The billed unit, and only the billed unit — same split as
    `extraction._raw_extraction_call`: nothing that can fail after the money
    is spent belongs in here, so parsing lives in `score_fit`."""
    response = client.complete(model=model, system=system, user=prompt)
    return LLMResult(
        output=response,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
    )


# One pre-decorated variant per ledger feature, same reasoning as
# extraction.py's `_CALL_BY_FEATURE`: separating production `score_fit` calls
# from `eval fit_scoring` traffic keeps prompt iteration off the
# per-application cost figure in `jscc costs`.
SCORING_FEATURE = "scoring"
SCORING_EVAL_FEATURE = "scoring_eval"

_CALL_BY_FEATURE = {
    SCORING_FEATURE: instrumented(SCORING_FEATURE)(_raw_scoring_call),
    SCORING_EVAL_FEATURE: instrumented(SCORING_EVAL_FEATURE)(_raw_scoring_call),
}


def score_fit(
    extracted: ExtractedJD,
    raw_jd_text: str,
    profile: Profile,
    *,
    conn: sqlite3.Connection | None = None,
    client: LLMClient | None = None,
    feature: str = SCORING_FEATURE,
) -> FitResult:
    """Score a JD's fit against a profile.

    `conn`, when passed, records the call to the `llm_calls` ledger (D5) via
    `@instrumented`, under `feature`. The `score` CLI command supplies one as
    `scoring`; `eval fit_scoring` as `scoring_eval`.

    The conn-less path remains for library and test callers, same as
    `extract_jd` — the honest scope of D5's claim is that calls made through
    a CLI command are instrumented, not every embedded caller.
    """
    client = client or default_scoring_client()

    payload = {
        "model": SCORING_MODEL,
        "system": SCORING_SYSTEM_PROMPT,
        "user": _build_user_prompt(extracted, raw_jd_text, profile),
        # Not flagged: a job posting and a candidate's own profile describe
        # roles and preferences, not a named third party. The sanitizer
        # redacts every payload unconditionally regardless of this flag
        # (D7 M5) -- that guarantee is what protects a `profile.private.yaml`
        # `display_name` or a JD forwarded with a recruiter's signature in
        # `raw_jd_text`, not this comment's reasoning.
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
        # Same reasoning as extraction's truncation check (gate finding M2):
        # the tokens were already spent, so this is a parse-error message,
        # not a retry -- raised after the ledger row would have been written.
        raise ScoringParseError(
            f"scoring response was truncated at the model's max_tokens limit "
            f"after {response.output_tokens} output tokens; the JSON is incomplete. "
            "Raise max_tokens on the client, or shorten what the prompt asks for."
        )
    return _parse_response(response.text)
