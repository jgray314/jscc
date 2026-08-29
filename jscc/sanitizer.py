"""LLM payload sanitizer (D7 M5) — Phase A6 hardened skeleton.

The sanitizer is the single choke point every LLM-bound payload passes through
before it leaves the process. In A4.5b this was a pass-through with a plain
`_sanitized_at` marker; the Phase A adversarial review flagged that marker as
forgeable (any caller could stamp a dict and pretend it went through the
choke point).

A6 replaces that with:

1. **A wrapper type** (`SanitizedPayload`) — Phase B code that sends to an LLM
   MUST accept a `SanitizedPayload`, not a `dict`. That's a type-level contract:
   you cannot pass a bare dict to the send-function without failing the type
   check (at runtime via `isinstance`, at author-time via a type checker).

2. **A per-process HMAC** over the payload contents, keyed by a secret generated
   at import time. `verify()` recomputes and constant-time compares. A forged
   `SanitizedPayload` with a bogus authenticator will not verify. The secret
   lives only in this process — copying a `SanitizedPayload` across processes
   invalidates it, which is what we want.

3. **Strict truthy refusal** — `bool(payload.get("contains_personal"))` catches
   `1`, `"true"`, `"yes"`, and any other truthy-but-not-`True` sentinel that
   a naive upstream builder might set.

4. **`SanitizerRefusal` inherits from `Exception`, not `ValueError`** — a
   generic `except ValueError:` in an upstream builder must not silently swallow
   a refusal. Refusal is a system-fatal condition: the payload contains data
   that never should have reached this function, and continuing risks a leak.

Actual redaction rules (name/email/phone scrubbing, per-role allowlists,
truncation) land in Phase B when the first prompt is written. The
`_transform` hook below is where they will attach; for A6 it's an identity.

See ADR-005 for the design record and rejected alternatives.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Process-scoped secret. Generated once per Python process; not persisted.
# A SanitizedPayload authenticated under one process's secret will not
# verify() under another's. That's intentional — the choke-point guarantee
# is "went through THIS process's sanitizer," which is exactly what an
# in-process safety architecture needs to prove.
_PROCESS_SECRET: bytes = secrets.token_bytes(32)


class SanitizerRefusal(Exception):
    """Raised when a payload is marked as containing personal data.

    Inherits from `Exception` (not `ValueError`) so a generic
    `except ValueError:` cannot silently swallow it. Refusal means the
    upstream builder produced unsafe content and continuing risks a leak.
    """


@dataclass(frozen=True)
class SanitizedPayload:
    """Opaque wrapper around a payload that has passed through the sanitizer.

    Phase B code MUST accept `SanitizedPayload`, not `dict`, at the LLM
    boundary. A `SanitizedPayload` constructed by hand with a forged
    authenticator will not `verify()`.

    Fields:
        data: the (currently pass-through) payload dict
        sanitized_at: UTC ISO-8601 timestamp
        authenticator: HMAC-SHA256 hex digest over stable-JSON(data) + sanitized_at
    """

    data: dict[str, Any]
    sanitized_at: str
    authenticator: str

    def __post_init__(self) -> None:  # pragma: no cover - trivial
        # Defensive: the dataclass is frozen but nothing stops a caller from
        # constructing one with garbage. verify() is the check that catches it;
        # nothing here should raise (that would let a bad construction crash
        # unrelated code paths).
        pass


def _stable_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON for HMAC input. sort_keys locks map ordering; the
    `default=str` fallback keeps this from crashing on non-serializable values
    inside the HMAC computation itself (verify would still reject a payload
    whose content couldn't be authenticated cleanly)."""
    return json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)


def _compute_authenticator(data: dict[str, Any], sanitized_at: str) -> str:
    msg = (_stable_json(data) + "|" + sanitized_at).encode("utf-8")
    return hmac.new(_PROCESS_SECRET, msg, hashlib.sha256).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _transform(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach point for Phase B redaction rules. A6 is identity."""
    return dict(payload)


def sanitize_for_llm(payload: dict[str, Any]) -> SanitizedPayload:
    """Return an authenticated `SanitizedPayload` for LLM consumption.

    Refuses (raises `SanitizerRefusal`) when the input payload has any truthy
    `contains_personal` flag. Passes the payload through `_transform` (A6:
    identity; Phase B: real redaction rules) and stamps a per-process HMAC.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be a dict, got {type(payload).__name__}")

    if bool(payload.get("contains_personal")):
        raise SanitizerRefusal(
            "payload flagged contains_personal (truthy); scrub upstream before "
            "sending to the sanitizer (D7 M5, D8)."
        )

    transformed = _transform(payload)
    stamped_at = _utcnow_iso()
    auth = _compute_authenticator(transformed, stamped_at)
    return SanitizedPayload(data=transformed, sanitized_at=stamped_at, authenticator=auth)


def verify(obj: Any) -> bool:
    """True iff `obj` is a `SanitizedPayload` whose authenticator matches this
    process's secret over its data + sanitized_at. Constant-time compare."""
    if not isinstance(obj, SanitizedPayload):
        return False
    expected = _compute_authenticator(obj.data, obj.sanitized_at)
    return hmac.compare_digest(expected, obj.authenticator)
