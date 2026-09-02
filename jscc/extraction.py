"""JD extraction — D9 step 1 of the split extract/score architecture.

Slice B2: real prompt + client plumbing. Every call routes through the full
D7/D8 choke point before anything touches the network: build a payload ->
`sanitize_for_llm` -> `send_to_llm` (raises `LLMSendError` if verification
fails) -> only then hand the verified dict to an `LLMClient`.

No `ANTHROPIC_API_KEY` is configured in this environment as of B2, so
`default_client()` resolves to `StubExtractionClient` — the prompt below is
authored and the whole pipeline is exercisable end-to-end, but live
iteration to the eval suite's >=80% target (per the sub-plan's DoD) is
blocked until a key is set. `python -m jscc eval jd_extraction` will report
a near-zero pass rate against the stub; that's expected, not a regression.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from .instrumentation import LLMResult, instrumented
from .llm_client import EXTRACTION_MODEL, LLMClient, default_client
from .models import ExtractedJD
from .sanitizer import send_to_llm, sanitize_for_llm

EXTRACTION_SYSTEM_PROMPT = """You are a job description parser. Given the raw text of a job posting, extract structured fields and return ONLY a JSON object — no prose, no markdown fences — matching this shape:

{
  "title": string,
  "level": one of "junior" | "mid" | "senior" | "staff" | "principal" | "director" — infer from title and seniority language if not stated explicitly; use "mid" as the default for an ambiguous individual-contributor role,
  "comp_band": string or null — only if the posting explicitly states a compensation range; never guess a figure,
  "location": string or null — city/state if given; null if remote-only or unstated,
  "remote_policy": one of "remote" | "hybrid" | "onsite", or null if not stated,
  "must_have_skills": array of short skill/technology strings pulled from the requirements section — technical requirements only, not soft skills,
  "responsibilities_summary": a 1-2 sentence summary of the role's core responsibilities, in your own words, not copied verbatim
}
"""


class ExtractionParseError(ValueError):
    """The model's response wasn't valid JSON matching ExtractedJD's shape."""


def _parse_response(text: str) -> ExtractedJD:
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionParseError(f"extraction response was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ExtractionParseError(
            f"extraction response was valid JSON but not an object: {type(data).__name__}"
        )
    try:
        return ExtractedJD(**data)
    except (TypeError, ValidationError) as e:
        raise ExtractionParseError(f"extraction response did not match ExtractedJD: {e}") from e


def _raw_extraction_call(
    conn: sqlite3.Connection, model: str, prompt: str, *, client: LLMClient, system: str
) -> LLMResult:
    response = client.complete(model=model, system=system, user=prompt)
    return LLMResult(
        output=_parse_response(response.text),
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
    )


# One pre-decorated variant per ledger feature. `@instrumented` fixes its label
# at decoration time, so separating production traffic from eval traffic means
# two wrappers over the same call, not a dynamic label. Keeping them apart
# matters because `jscc costs` is a portfolio artifact (D5/C3) — prompt
# iteration would otherwise inflate the per-application cost figure with runs
# that never produced an application.
EXTRACTION_FEATURE = "extraction"
EXTRACTION_EVAL_FEATURE = "extraction_eval"

_CALL_BY_FEATURE = {
    EXTRACTION_FEATURE: instrumented(EXTRACTION_FEATURE)(_raw_extraction_call),
    EXTRACTION_EVAL_FEATURE: instrumented(EXTRACTION_EVAL_FEATURE)(_raw_extraction_call),
}


def extract_jd(
    raw_text: str,
    *,
    conn: sqlite3.Connection | None = None,
    client: LLMClient | None = None,
    feature: str = EXTRACTION_FEATURE,
) -> ExtractedJD:
    """Extract structured fields from a raw JD.

    `conn`, when passed, records the call to the `llm_calls` ledger (D5) via
    `@instrumented`, under `feature`. Every CLI path supplies one — `ingest`
    and `resolve-dlq` as `extraction`, `eval jd_extraction` as
    `extraction_eval`.

    Gate finding M1: the eval command used to call without a `conn` on the
    reasoning that eval runs measure prompt quality, not production cost.
    True, but it made prompt iteration — the phase that burns the most
    tokens — the one phase with no cost record, while D5 claimed every LLM
    call was instrumented and D7 promised budget caps enforced through that
    same instrumentation. Eval runs are now metered under their own feature
    label so they show up in `jscc costs` without polluting the
    per-application figure.

    The conn-less path remains for library and test callers, which have no
    ledger to write to. That is the honest scope of D5's claim: calls made
    through a CLI command are instrumented; an embedded caller that supplies
    no database cannot be.
    """
    client = client or default_client()

    payload = {
        "model": EXTRACTION_MODEL,
        "system": EXTRACTION_SYSTEM_PROMPT,
        "user": raw_text,
        # Not flagged: a job posting describes a role, not a named individual.
        # But this flag is NOT what protects the call. `raw_text` here can be
        # arbitrary pasted text (`ingest --paste`, `resolve-dlq --paste-text`),
        # and a JD forwarded from a recruiter's email carries their name,
        # address, and number in the signature. The sanitizer redacts every
        # payload unconditionally regardless of this flag (D7 M5) — that is
        # the guarantee. Gate finding C1: before the B5 slice it did not, and
        # this comment's reasoning was the whole defense.
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
        return call(
            conn, verified["model"], verified["user"], client=client, system=verified["system"]
        )
    response = client.complete(model=verified["model"], system=verified["system"], user=verified["user"])
    return _parse_response(response.text)
