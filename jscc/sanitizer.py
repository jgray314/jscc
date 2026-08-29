"""LLM payload sanitizer (D7 M5) — Phase A skeleton.

The purpose of this module is to be the single choke point every LLM-bound
payload passes through before it leaves the process. In A4.5b it's a
pass-through with a marker stamp: the interface is locked so Phase B slices
can import it stably, and the marker is verifiable in tests / traces to prove
"nothing bypassed the choke point."

Real redaction rules (name/email/phone scrubbing, per-role allowlists,
truncation) land in Phase B when the first prompt is written and there is
concrete signal for what rules matter. See D7 and D8 in the parent plan.

Two invariants A4.5b enforces:
1. Every returned payload carries a ``_sanitized_at`` UTC ISO-8601 timestamp.
2. A payload flagged ``contains_personal: true`` is refused outright — even
   the pass-through will not stamp it. That flag is the caller's contract
   that the content is known-unsafe and must be pre-scrubbed upstream.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MARKER_KEY = "_sanitized_at"


class SanitizerRefusal(ValueError):
    """Raised when a payload is marked as containing personal data.

    Callers should treat this as a bug in the upstream builder: personal data
    should have been redacted before reaching the sanitizer, not passed
    through with the flag set.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_for_llm(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized copy of ``payload`` for LLM consumption.

    Skeleton: pass-through + marker. Refuses if ``contains_personal`` is true.

    The returned dict is a shallow copy — callers must not mutate nested
    structures and expect the marker to remain accurate.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be a dict, got {type(payload).__name__}")

    if payload.get("contains_personal") is True:
        raise SanitizerRefusal(
            "payload flagged contains_personal=True; scrub upstream before "
            "sending to the sanitizer (D7 M5, D8)."
        )

    out = dict(payload)
    out[MARKER_KEY] = _utcnow().isoformat()
    return out


def is_sanitized(payload: dict[str, Any]) -> bool:
    """Cheap check: does this payload carry the sanitizer marker?"""
    return isinstance(payload, dict) and MARKER_KEY in payload
