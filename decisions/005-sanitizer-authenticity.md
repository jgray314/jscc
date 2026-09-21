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
  convention.
- Positive: the wrapper type makes the LLM-send function's signature
  self-documenting — `def send(prompt: SanitizedPayload)` is unambiguous.
- Positive: the truthy refusal catches naive-upstream bugs without adding a
  full type system.
- Cost: Phase B code that used to `payload["field"]` now does
  `sanitized.data["field"]` — one extra `.data` hop. Acceptable.
- Cost: `SanitizedPayload` is serializable but the HMAC is process-local, so
  a payload persisted and reloaded in a different process cannot re-verify.
  That's the intended semantics; documented in `send_to_llm`'s docstring.
  **Gate finding L-sanitizer-1:** flagging explicitly for whoever builds
  Phase C/D worker-pool plumbing — a `SanitizedPayload` handed to a worker
  process (via `multiprocessing`, a task queue, anything that pickles
  across a process boundary) will fail `verify()` there even though nothing
  about it was forged. Sanitize inside the worker process, not before
  crossing into it.
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

## Addendum (2026-09-12) — gate finding M-5, decided not extended

The Phase B → C rerun gate (9/4) found that the type-level choke point this
ADR describes stops at `send_to_llm`: its caller unpacks the verified dict
into three bare `str` kwargs (`model=`, `system=`, `user=`) to call
`LLMClient.complete`, so nothing at the type level (or at runtime) stops a
future caller from assembling those three strings itself and calling
`complete` directly, skipping `sanitize_for_llm`/`send_to_llm` entirely.

**Decided: not extended now.** `extraction.py` is still the only caller.
Wrapping `complete`'s three parameters in a second authenticated type,
mirroring `SanitizedPayload`, would be solving for D9's scorer and D10's
router/composer — call sites that don't exist yet, in phases that haven't
started. That is designing for a hypothetical requirement rather than a real
one, and the extra hop (`verified["model"]` → `some_wrapper.model`, plus a
second `verify()`-shaped check at the socket boundary) is cost paid today
for a benefit that starts on the day a second caller is written, not before.

**Revisit exactly when:** the moment `extraction.py` stops being the only
module that calls `.complete()` — i.e., when D9's scorer or D10's
router/composer lands. At that point the choke point genuinely needs to
extend past `send_to_llm`, because "convention holds, there's one caller" is
no longer true. Wrap `LLMClient.complete`'s three kwargs in a single typed
request object at that point, not before.

## Addendum (2026-09-20) — the revisit trigger had fired; held by inspection, not by type

The trigger above fired when the scorer landed in Phase C, and again with the
router and composer in Phase D: four modules now call `LLMClient.complete`
(`extraction`, `scoring`, `routing`, `composition`). It was not acted on at the
time, and the Phase C → D gate traced the call sites that existed then, so the
routing and composition callers had never been reviewed for this. Writing the
threat model (`docs/threat-model.md`, T8) surfaced it.

**Decided: enforce by test now, still not by type.** `tests/test_llm_egress.py`
fails when a module outside the known four calls the client; when a stage's entry
function verifies before it sanitizes, reaches the client before verifying, or
hands the client anything not read out of the verified payload; and when a stage
still reaches its client after the send boundary refuses. Each of those three was
checked by injecting the regression and watching a test fail.

**Why not the typed request object this ADR anticipated.** The test closes the
gap that mattered (a caller that skips the boundary) at a fraction of the
change, and it fails at CI time instead of relying on a reviewer noticing. What
it does not do is stop a caller that passes the checks by being written to look
like the others; an authenticated argument type would. That remains the stronger
fix and the right one if the number of callers grows or anyone other than the
author writes one.

**Revisit when:** a fifth caller is added, a caller is not shaped like the
existing four, or the inspection test needs a special case to pass.

## Second addendum (2026-09-20, Phase D gate) — one call path

The Phase D gate found two things the inspection test above did not cover. The
scan listed only the top level of `jscc/`, so it stopped seeing the CLI the day
the CLI became a package. And the four stages each carried their own copy of the
sanitize, verify, record and truncation steps, which the test checked line by line.

**Decided:** the four stages now call the model through one function,
`jscc/stage_call.py`'s `call_stage`, and that module is the only one allowed to
call `LLMClient.complete`. The scan walks subpackages and has its own test that a
nested caller is found. This is still inspection rather than a type, but the
property checked is now "one module calls the client", which a new caller cannot
satisfy by imitating the others.

The same change moved serialization after redaction: each string field is redacted
on its own, and the prompt text is produced from the verified payload. Redacting the
serialized JSON had let non-ASCII danger-list names through (they were compared
against their `\uXXXX` escapes) and could corrupt the JSON itself. The recorded eval
prompts are byte-identical before and after, confirmed by replaying all four suites.

**Revisit when:** a caller needs something `call_stage` does not provide, or anyone
other than the author adds a stage.

