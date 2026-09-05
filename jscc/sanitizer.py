"""LLM payload sanitizer (D7 M5) — the single LLM-egress choke point.

Every LLM-bound payload passes through here before it leaves the process. An
earlier version marked sanitized payloads with a plain `_sanitized_at` field,
which any caller could stamp onto a dict to fake having passed through. A
marker anyone can write is not a guarantee. What replaced it:

1. **A wrapper type** (`SanitizedPayload`) — code that sends to an LLM MUST
   accept a `SanitizedPayload`, not a `dict`. That's a type-level contract: you
   cannot pass a bare dict to the send-function without failing the type check
   (at runtime via `isinstance`, at author-time via a type checker). The
   contract binds at `send_to_llm`; the HTTP client underneath it still takes
   bare strings, so today the guarantee rests on `send_to_llm` being the only
   route callers take to reach it.

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

5. **Unconditional redaction.** `_transform` rewrites every
   personal-data-shaped span using the same pattern definitions as the
   pre-commit scanner (`jscc/personal_data.py`), *before* the HMAC is
   computed — so the authenticator covers the redacted bytes and no verified
   path can carry the original text. This is what backs D7 M5 and D8; an
   authenticated payload that was never rewritten would be a signature over
   a leak.

   The scope of that guarantee is stated narrowly and deliberately in
   `personal_data.py`: structured identifiers and known names, not arbitrary
   free-text name detection.

See ADR-005 for the design record and rejected alternatives.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .personal_data import default_danger_terms, redact

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


# Payload keys holding application-authored control values rather than user
# content. `model` is excluded from redaction because model ids carry a
# date-shaped digit run that the phone heuristic matches — redacting it would
# rewrite the model name and break the call, and it is never user content.
# (Same false-positive class documented in `llm_client.py`.) Deliberately as
# small as possible: `system` is app-authored too but is left in scope, since
# redacting it is a no-op today and a carve-out is how holes start.
_CONTROL_KEYS = frozenset({"model"})


def _redact_tree(value: Any, key: str | None, danger_terms: list[str],
                 name_roles: Mapping[str, str] | None) -> Any:
    """Walk a JSON-native snapshot, rewriting every in-scope string."""
    if isinstance(value, dict):
        return {
            k: _redact_tree(v, k, danger_terms, name_roles) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_tree(v, key, danger_terms, name_roles) for v in value]
    if isinstance(value, str) and key not in _CONTROL_KEYS:
        return redact(value, danger_terms=danger_terms, name_roles=name_roles)
    return value


def _transform(
    payload: dict[str, Any], name_roles: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Snapshot the payload, then redact it (D7 M5).

    **Snapshot.** A6 was identity via `dict(payload)`, but that was a shallow
    copy: nested lists / dicts stayed aliased to the caller's references, so a
    caller holding a nested container could mutate it between `verify()` and
    the LLM send — the authenticator would still match at verify time, but the
    bytes on the wire would be whatever the caller wrote last. Routing through
    the same canonical JSON that the HMAC and `verify` use deep-copies every
    container and forbids sharing any mutable object with the caller.

    **Redact.** Every string in the payload is rewritten using the same
    pattern definitions as the pre-commit scanner (`jscc/personal_data.py`).
    Stopping at the snapshot would leave D7 M5 and D8 describing behavior the
    code does not have.

    Redaction is **unconditional**. It does not consult `contains_personal`,
    and no caller can opt out. That is the difference between structural and
    disciplinary: the guarantee must not depend on an upstream builder setting
    a flag correctly, because `extract_jd` hardcodes that flag to False and a
    pasted JD from a recruiter's email is exactly the case it would miss.

    Redaction runs *before* the authenticator is computed, so the HMAC covers
    the redacted bytes and there is no path that ships the original text.
    """
    snapshot = json.loads(_stable_json(payload))
    return _redact_tree(snapshot, None, default_danger_terms(), name_roles)


def sanitize_for_llm(
    payload: dict[str, Any], *, name_roles: Mapping[str, str] | None = None
) -> SanitizedPayload:
    """Return an authenticated, redacted `SanitizedPayload` for LLM consumption.

    Two independent protections, in this order:

    1. **Refusal** — raises `SanitizerRefusal` on any truthy `contains_personal`
       flag. An explicit upstream signal that the payload should never have
       been built.
    2. **Redaction** — `_transform` rewrites every personal-data-shaped span it
       can detect, unconditionally, whether or not the flag was set.

    (2) is the structural one. (1) depends on an upstream builder being right;
    (2) does not, which is why it does not consult the flag.

    `name_roles` is an optional mapping of known contact names to their roles
    (e.g. `{"Dana Reyes": "recruiter"}` -> `[contact:recruiter]`). Callers that
    hold contact records — Phase D's drafter — pass it to get D7 M5's role-token
    substitution. Omitting it weakens nothing that the pattern rules already
    cover; it only means unknown names in free prose stay as written, which is
    the documented boundary in `personal_data.py`.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be a dict, got {type(payload).__name__}")

    if bool(payload.get("contains_personal")):
        raise SanitizerRefusal(
            "payload flagged contains_personal (truthy); scrub upstream before "
            "sending to the sanitizer (D7 M5, D8)."
        )

    transformed = _transform(payload, name_roles)
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


class LLMSendError(Exception):
    """Raised when `send_to_llm` refuses to ship a payload.

    Distinct from `SanitizerRefusal` (upstream flagged personal data before
    sanitization). This fires at the send boundary: the wrapper's HMAC did
    not verify under this process's secret, or the caller passed a bare
    dict / forged object. Either way the payload does not leave the process.
    """


def send_to_llm(payload: SanitizedPayload) -> dict[str, Any]:
    """The send boundary: verifies the wrapper, then returns its data snapshot
    for the caller to hand to the model client.

    The network call itself lives in `llm_client`, not here. Keeping this a
    verification gate rather than growing it into the thing that opens a socket
    means the check that a payload was really sanitized stays separable from
    the transport it goes out over.

    The type annotation forces callers to pass `SanitizedPayload`, not `dict`.
    The runtime `verify()` check catches:
      - callers who cast a bare dict via `typing.cast` and slipped past mypy,
      - forged `SanitizedPayload` objects constructed with a bogus HMAC,
      - `SanitizedPayload` objects that authenticated under a different
        process's secret (cross-process copy, pickle-and-reload).

    Raises `LLMSendError` on any of those. Callers MUST NOT catch and
    continue — a failed verify at the send boundary is a system-fatal
    signal that the D8 guarantee is broken for this payload.

    Without a callsite the type contract has no teeth: `SanitizedPayload`
    would be a wrapper nothing consumes, and the D8 claim would be a comment.
    This is the callsite.
    """
    if not verify(payload):
        raise LLMSendError(
            "send_to_llm: payload failed verify(). The wrapper's HMAC did not "
            "match this process's secret over its data + sanitized_at. This is "
            "either a forged SanitizedPayload, a bare dict passed via cast, or "
            "a payload constructed in a different process. Refusing to send."
        )
    return payload.data
