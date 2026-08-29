from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FetchStatus(str, Enum):
    ok = "ok"
    dlq_paywall = "dlq_paywall"
    dlq_blocked = "dlq_blocked"
    dlq_timeout = "dlq_timeout"
    dlq_extraction_failed = "dlq_extraction_failed"
    manual = "manual"


class ContactRole(str, Enum):
    recruiter = "recruiter"
    hm = "hm"
    ic = "ic"
    referrer = "referrer"
    other = "other"


class InteractionType(str, Enum):
    applied = "applied"
    recruiter_reply = "recruiter_reply"
    screen = "screen"
    onsite = "onsite"
    offer = "offer"
    rejection = "rejection"
    custom = "custom"


class FailureMode(str, Enum):
    paywall = "paywall"
    blocked = "blocked"
    timeout = "timeout"
    extraction_failed = "extraction_failed"
    other = "other"


class Resolution(str, Enum):
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


class LLMCallRecord(BaseModel):
    """One row per LLM call, captured by the `@instrumented` decorator (D5).

    Exists ahead of any real LLM call (Phase B) on purpose: instrumentation
    is Phase A foundation so every call from B2 onward is caught from day one.
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
