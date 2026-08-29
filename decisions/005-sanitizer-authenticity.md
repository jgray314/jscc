# ADR 005: sanitizer authenticity — wrapper type + per-process HMAC

**Status:** Accepted (2026-08-28, Slice A6 — Phase A hardening)

## Context

The A4.5b sanitizer skeleton stamped a `_sanitized_at` marker on the returned
dict and offered an `is_sanitized(payload)` check. The Phase A adversarial
review flagged that any caller could forge the marker (`payload["_sanitized_at"]
= "..."`) and defeat the choke-point guarantee — the whole point of the
sanitizer is that "went through the sanitizer" must be provable at the LLM
boundary, and a forgeable marker proves nothing.

D7 M5 is not optional. Phase B will introduce the first LLM call, and every
LLM-bound payload must be provably sanitized before it leaves the process.
Getting the authenticity contract right now — before any Phase B code
inherits a weak guarantee — is cheaper than retrofitting later.

## Decision

Three coupled mechanisms:

1. **Wrapper type.** `sanitize_for_llm(dict) -> SanitizedPayload`. A frozen
   dataclass. Phase B's LLM-send function will accept only
   `SanitizedPayload`, not `dict`. That's a type-level contract: passing a
   bare dict to the send-function is a type error at author-time and an
   `isinstance` failure at runtime.

2. **Per-process HMAC.** A 32-byte secret generated at module import via
   `secrets.token_bytes`. The wrapper carries an HMAC-SHA256 over
   stable-JSON(data) + sanitized_at, keyed by that secret. `verify(obj)`
   recomputes and uses `hmac.compare_digest` (constant-time). A forged
   `SanitizedPayload` with a bogus authenticator fails verify. Mutating
   `data` or `sanitized_at` after construction also fails verify — the
   HMAC covers both.

3. **Strict truthy refusal + `Exception` parent for `SanitizerRefusal`.** The
   `is True` check missed `1`, `"true"`, `"yes"`, and other truthy sentinels;
   fixed to `bool(...)`. The refusal class inherits from `Exception`, not
   `ValueError` — a generic `except ValueError:` in an upstream builder must
   not silently swallow a refusal, because that would let a leak pass under
   a normal-looking error-handling path.

## Alternatives considered

- **`isinstance` check only, no HMAC.** Simpler, but any caller can
  `SanitizedPayload(data={}, sanitized_at="...", authenticator="")` and pass
  the isinstance check. Rejected — the entire point is that construction
  outside `sanitize_for_llm` should not pass verification.
- **Module-visibility hack (single underscore, "trust the convention").**
  Python doesn't enforce private access; a determined caller (or an LLM
  writing test code) will reach for `_PrivateClass` without realizing it's
  the safety boundary. Rejected.
- **Marker-only, no wrapper.** What A4.5b did. Fails to the forgery attack.
  Rejected.
- **Subclass of `dict`.** Would let Phase B code treat the payload as a dict
  directly. But `dict` subclasses are trivially forgeable
  (`class FakeSanitized(dict): pass; FakeSanitized({...})`). Also loses the
  `frozen=True` invariant. Rejected.
- **Persistent secret in a file.** Would let a copied `SanitizedPayload`
  verify across processes. Rejected — the guarantee we want is "this
  specific process's sanitizer stamped this," and a cross-process replay
  is a leak vector we'd rather refuse than accept.
- **Global signature registry / OTP-style nonce.** Overkill for a
  single-user in-process choke point. HMAC over payload+timestamp with a
  process-scoped key gets the same authenticity guarantee at far lower
  complexity.

## Consequences

- Positive: forgery attempts are structurally caught by `verify()`, not by
  convention. Reviewer signal: this is what "structural safety over
  discipline" looks like at the code level, not just the architecture level.
- Positive: the wrapper type makes the LLM-send function's signature
  self-documenting — `def send(prompt: SanitizedPayload)` is unambiguous.
- Positive: the truthy refusal catches naive-upstream bugs without adding a
  full type system.
- Cost: Phase B code that used to `payload["field"]` now does
  `sanitized.data["field"]` — one extra `.data` hop. Acceptable.
- Cost: `SanitizedPayload` is serializable but the HMAC is process-local, so
  a payload persisted and reloaded in a different process cannot re-verify.
  That's the intended semantics; document it in the module docstring.
- Cost: `verify()` recomputes an HMAC on every call. SHA-256 over a
  typical prompt is microseconds — not on any hot path.
- Revisit if: cross-process payload passing becomes a real need (Phase E
  might have this for a web UI worker split) — a persistent-key mode with
  key rotation would replace the process-secret. Not needed for v1.

## Related

- ADR-003 (mode isolation) — same architectural pattern: structural
  refusal, not disciplined-check-that-the-caller-remembers.
- A6 also fixes storage-side mode-marker holes (C4/C5/C6) — see the A6
  CHANGELOG entry.
- Follow-on for Phase B: the actual `send_to_llm()` function will accept
  only `SanitizedPayload` and call `verify()` as its first line, refusing
  any object that fails.
