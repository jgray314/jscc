from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class FetchStatus(StrEnum):
    ok = "ok"
    dlq_paywall = "dlq_paywall"
    dlq_blocked = "dlq_blocked"
    dlq_timeout = "dlq_timeout"
    dlq_extraction_failed = "dlq_extraction_failed"
    manual = "manual"


class ContactRole(StrEnum):
    recruiter = "recruiter"
    hm = "hm"
    ic = "ic"
    referrer = "referrer"
    other = "other"


class InteractionType(StrEnum):
    applied = "applied"
    recruiter_reply = "recruiter_reply"
    screen = "screen"
    onsite = "onsite"
    offer = "offer"
    rejection = "rejection"
    custom = "custom"


class FailureMode(StrEnum):
    paywall = "paywall"
    blocked = "blocked"
    timeout = "timeout"
    extraction_failed = "extraction_failed"
    other = "other"


class Resolution(StrEnum):
    unresolved = "unresolved"
    manual_paste = "manual_paste"
    wont_fix = "wont_fix"


class Application(BaseModel):
    id: str = Field(default_factory=_new_id)
    source_url: str | None = None
    source_raw: str = ""
    fetch_status: FetchStatus = FetchStatus.ok
    title: str
    company: str
    extracted_jd: dict[str, Any] | None = None
    stage: str
    fit_score: float | None = None
    fit_rationale: str | None = None
    applied_at: date | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_interaction_at: datetime | None = None


class Contact(BaseModel):
    id: str = Field(default_factory=_new_id)
    application_id: str
    name: str
    role: ContactRole
    notes: str | None = None


class Interaction(BaseModel):
    id: str = Field(default_factory=_new_id)
    application_id: str
    contact_id: str | None = None
    type: InteractionType
    occurred_at: datetime
    notes: str = ""
    next_action: str | None = None
    next_action_due: date | None = None


class DLQEntry(BaseModel):
    id: str = Field(default_factory=_new_id)
    application_id: str | None = None
    source_url: str
    failure_mode: FailureMode
    attempted_at: datetime = Field(default_factory=_now)
    error_detail: str = ""
    resolution: Resolution = Resolution.unresolved
    resolved_at: datetime | None = None


class ExtractedJD(BaseModel):
    """Structured output of `extract_jd` (D9 step 1). Stored in
    `Application.extracted_jd` as a plain dict; this model is the contract
    the extraction prompt (Slice B2) is written against and the eval suite
    (Slice B1) grades against.

    `level` and `remote_policy` are the prompt's two
    closed vocabularies (six levels, three remote policies) and `evals.py`
    grades both as exact matches on that assumption -- but the type here is
    bare `str`, so nothing stops an out-of-vocabulary value from parsing and
    getting stored. Decided: leave it a `str`. The eval suite is what
    actually enforces the vocabulary today, at the sample the suite covers (36 cases);
    a `Literal` would extend that enforcement to every live `ingest` call
    (turning a bad value into a DLQ entry instead of a silently wrong stored
    field), but that's more machinery than the disclosed residual
    ("ambiguous-title leveling") currently justifies. Revisit if a live run
    stores an out-of-vocabulary value somewhere this matters.
    """

    title: str
    company: str | None = None
    level: str
    comp_band: str | None = None
    location: str | None = None
    remote_policy: str | None = None
    must_have_skills: list[str] = Field(default_factory=list)
    responsibilities_summary: str


class FitResult(BaseModel):
    """Structured output of `score_fit` (D9 step 2). The contract Slice C2's
    prompt is written against and the eval suite (Slice C1) grades against.

    `score` is bounded 0-100 per the scoring prompt's own contract: `json.loads` accepts `NaN`/`Infinity` by
    default, and nothing upstream of this model checked the range against a
    live model's response -- only `evals.py`'s fixture grading did, which
    never runs against a live `score` CLI call. `Field(ge=0, le=100)` rejects
    NaN/Infinity too, since every comparison against either is False and the
    bound check fails either way -- verified directly, not assumed.
    """

    score: float = Field(ge=0, le=100)
    rationale: str


class RoutingClassification(StrEnum):
    routine = "routine"
    non_routine = "non_routine"


class RoutingDecision(BaseModel):
    """Structured output of `route_followup` (D10 step 1). The contract
    the routing prompt is written against and the routing eval suite grades
    against.

    Two shapes per D10, not one field set used inconsistently: a `routine`
    decision carries `intent` (what routine situation this is -- e.g.
    "post_interview_thank_you") and leaves `reason`/`considerations` unset.
    A `non_routine` decision carries `reason` and `considerations` instead
    and leaves `intent` unset -- there is no drafted intent for a situation
    the router is refusing to auto-draft. Both fields being optional on one
    model (rather than a tagged union) matches how `ExtractedJD`/`FitResult`
    are already parsed straight out of the model's JSON response.
    """

    classification: RoutingClassification
    intent: str | None = None
    reason: str | None = None
    considerations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _routine_is_unhedged(self) -> RoutingDecision:
        """A routine answer must name an intent and flag nothing.

        "Routine" is the one answer that leads to an automatic draft, so an answer
        that says routine while giving a reason or considerations, or naming no
        intent, is treated as malformed rather than read either way. Non-routine
        answers are not checked: every reading of one ends at a person.
        """
        if self.classification == RoutingClassification.routine:
            if not (self.intent or "").strip():
                raise ValueError("a routine decision must name an intent")
            if (self.reason or "").strip() or self.considerations:
                raise ValueError("a routine decision cannot carry a reason or considerations")
        return self


class DraftEmail(BaseModel):
    """Structured output of `compose_followup` (D10 step 2A). The contract
    the composition prompt is written against and the composition eval suite
    grades against.

    Only reached when `route_followup` classifies a situation `routine` --
    `non_routine` situations get a briefing card instead, never a draft.
    `subject`/`body` mirror how an application/history-driven prompt (per
    D9/D10's existing shape) is expected to produce something pasteable
    straight into an email client, not a structured object requiring further
    assembly.

    `needs_input` is the composer's escape hatch: when the reply would
    need a detail the input does not supply (a dietary answer, which of several
    proposed times), the composer names that detail here instead of writing a
    draft, `subject`/`body` stay empty, and `followup` turns it into a briefing.
    It is the defense-in-depth layer behind the router's missing-information
    rule, not a replacement for it.
    """

    subject: str = ""
    body: str = ""
    needs_input: str | None = None


class Briefing(BaseModel):
    """The non-routine half of `followup`'s answer (D10 step 2B): a card for a
    human, never a draft.

    Assembled deterministically from a `non_routine` `RoutingDecision` plus the
    application it was routed for -- no LLM call, so nothing here goes through
    the sanitizer. `handle_manually` is always true; it exists so a consumer
    (the CLI today; the dashboard does not render drafts) can key on the field instead
    of inferring "no draft" from the type.
    """

    application_id: str
    company: str
    title: str
    stage: str
    source_url: str | None = None
    reason: str
    considerations: list[str] = Field(default_factory=list)
    handle_manually: bool = True


class LLMCallRecord(BaseModel):
    """One row per LLM call, captured by the `@instrumented` decorator (D5).

    Exists ahead of any real LLM call (Phase B) on purpose: instrumentation
    is Phase A foundation so every call from B2 onward is caught from day one.

    `error`, when set, marks a row written for a call that raised before
    `LLMResult` could be built -- a connection reset or read-timeout mid-call,
    possibly after Anthropic already generated/billed tokens. `input_tokens`/`output_tokens`/`cost_usd` are 0 on
    such a row because the real figures were never returned; the point of the
    row is that the attempt is visible at all in a ledger whose stated
    purpose is cost transparency, not that its cost is known. A separate rule
    covers the sibling case -- a billed call that returns cleanly and then
    fails to *parse* -- which already gets real token counts, since the
    response was fully received.
    """

    id: str = Field(default_factory=_new_id)
    feature: str
    model: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    ts: datetime = Field(default_factory=_now)
    error: str | None = None
