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
  "company": string or null — the hiring company's name, if the posting states it; null if the posting never names the employer,
  "level": one of "junior" | "mid" | "senior" | "staff" | "principal" | "director" — infer from title and seniority language if not stated explicitly; use "mid" as the default for an ambiguous individual-contributor role. A people-manager title ("Engineering Manager", "Manager, ...") maps to at least "senior" regardless of how few years of management experience are stated — managing a team carries scope beyond an individual contributor at the same tenure. A "Director"-titled role maps to "director". A "Founding Engineer" or first-engineering-hire role maps to "staff" unless the posting describes senior-leadership-level scope, in which case use "principal" or "director",
  "comp_band": string or null — any explicitly stated compensation figure: a range, a single dollar amount, or an hourly/contract rate all count. Never guess or infer a figure that isn't stated,
  "location": string or null — a city, state, or metro area if given. null if remote-only, unstated, or if only a country or multi-country region is given (e.g. "US", "Remote — US or Canada") — a bare country name is not a location for this field,
  "remote_policy": one of "remote" | "hybrid" | "onsite", or null if not stated,
  "must_have_skills": array of short skill/technology strings — technical requirements only, not soft skills. Rules:
    - Pull only from an explicit requirements/qualifications section (e.g. "Requirements", "Who You Are", "What we're looking for", "About You") — never from a "tech stack" or "about the company" paragraph that describes the team's tooling rather than what's required of the candidate.
    - Exclude anything explicitly marked optional or secondary (a "Preferred" section, or a clause qualified with "ideally", "a plus", "nice to have").
    - A bare years-of-experience number by itself is never a skill entry (don't add "8+ years" as an item). When the years-of-experience phrase names a domain that just restates the job title itself ("backend experience" on a Backend Engineer posting, "ML experience" on an ML Engineer posting, "security engineering" on a Security Engineer posting, "QA automation" on a QA Automation Engineer posting), don't extract that either — it's redundant with the title, not new information. DO extract a distinct competency named alongside years when it is NOT just the title restated ("prior experience managing managers" on a Director role, "ML domain fluency" on a non-ML-titled role, "causal inference expertise"). Concrete named technologies and tools (e.g. "PyTorch", "Kubernetes") are always extracted regardless of this rule. This exclusion applies only to the specific years-of-experience clause itself — a requirements line is often several comma- or bullet-separated clauses (e.g. "2+ years as an engineering manager, prior IC background in backend systems"), and a leading years-clause does not disqualify the other clauses in the same line. Evaluate each clause on its own.
    - Exclude soft or aptitude descriptors ("systems design instincts", "comfort operating with ambiguity", "strong communication skills") even when they sit inside the requirements section,
    - A single requirement naming two distinct skills joined by "and" ("familiarity with the OWASP Top 10 and common web application vulnerability classes", "experience with model evaluation and auditability practices") is two skill entries, not one — extract both, not just the first.
  "responsibilities_summary": a 1-2 sentence summary of the role's core responsibilities, in your own words, not copied verbatim. Always a non-empty string, even for a minimal posting with almost no detail — give your best one-sentence characterization rather than returning null or omitting it
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
    """The billed unit, and only the billed unit.

    Nothing that can fail *after* the money is spent belongs in here: the
    ledger row is written when this returns, so parsing lives in `extract_jd`.
    A malformed response is the likeliest failure while iterating on a prompt,
    which is exactly when the cost figures are being read. Keeping the
    boundary here also makes recorded latency the network call alone.
    """
    response = client.complete(model=model, system=system, user=prompt)
    return LLMResult(
        output=response,
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

    Eval runs are metered under their own feature label. Prompt iteration
    burns the most tokens of any phase, so leaving it off the ledger would
    make D5's claim and D7's budget caps both untrue; folding it into
    `extraction` would inflate the per-application cost figure.

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
        # the guarantee, and this comment's reasoning is not.
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
        # Checked here, not inside the instrumented call: the tokens were
        # spent, so the ledger row must be written first (finding M2). Raised
        # as a parse error because that is what it is downstream -- incomplete
        # JSON -- but the message says truncation so prompt iteration does not
        # chase a prompt bug that is really a max_tokens ceiling.
        raise ExtractionParseError(
            f"extraction response was truncated at the model's max_tokens limit "
            f"after {response.output_tokens} output tokens; the JSON is incomplete. "
            "Raise max_tokens on the client, or shorten what the prompt asks for."
        )
    return _parse_response(response.text)
