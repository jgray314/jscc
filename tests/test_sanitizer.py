from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from jscc.sanitizer import (
    SanitizedPayload,
    SanitizerRefusal,
    sanitize_for_llm,
    verify,
)


def test_returns_sanitized_payload_wrapper() -> None:
    out = sanitize_for_llm({"role": "SWE", "company": "ExampleCo"})
    assert isinstance(out, SanitizedPayload)
    # Payload dict is preserved
    assert out.data["role"] == "SWE"
    assert out.data["company"] == "ExampleCo"
    # ISO-8601 UTC timestamp
    parsed = datetime.fromisoformat(out.sanitized_at)
    assert parsed.tzinfo is not None


def test_verify_true_for_real_output() -> None:
    out = sanitize_for_llm({"a": 1})
    assert verify(out) is True


def test_verify_false_for_bare_dict() -> None:
    """C2 regression: a bare dict must not pass verify(), even one shaped like a payload."""
    assert verify({"data": {"a": 1}, "sanitized_at": "2026-01-01T00:00:00+00:00"}) is False


def test_verify_false_for_forged_authenticator() -> None:
    """C2 regression: hand-constructed SanitizedPayload with a bogus HMAC fails."""
    forged = SanitizedPayload(
        data={"a": 1},
        sanitized_at="2026-01-01T00:00:00+00:00",
        authenticator="0" * 64,
    )
    assert verify(forged) is False


def test_verify_false_after_data_mutation_via_reconstruction() -> None:
    """C2: if a caller reconstructs the payload with mutated data but reuses the
    real authenticator, verification fails (HMAC covers the data)."""
    real = sanitize_for_llm({"a": 1})
    tampered = SanitizedPayload(
        data={"a": 999},  # changed
        sanitized_at=real.sanitized_at,
        authenticator=real.authenticator,
    )
    assert verify(tampered) is False


def test_verify_false_after_timestamp_swap() -> None:
    """C2: HMAC covers sanitized_at too — swapping the timestamp breaks verify."""
    real = sanitize_for_llm({"a": 1})
    tampered = SanitizedPayload(
        data=real.data,
        sanitized_at="2099-12-31T00:00:00+00:00",
        authenticator=real.authenticator,
    )
    assert verify(tampered) is False


def test_verify_false_for_none_and_str() -> None:
    assert verify(None) is False
    assert verify("not a payload") is False
    assert verify(42) is False


def test_refuses_contains_personal_true() -> None:
    with pytest.raises(SanitizerRefusal, match="contains_personal"):
        sanitize_for_llm({"contains_personal": True, "body": "..."})


@pytest.mark.parametrize("value", [1, "true", "yes", "1", [1], {"a": 1}])
def test_refuses_all_truthy_contains_personal_values(value: Any) -> None:
    """C1 regression: `is True` was too strict — must use bool()."""
    with pytest.raises(SanitizerRefusal):
        sanitize_for_llm({"contains_personal": value, "body": "..."})


@pytest.mark.parametrize("value", [False, 0, "", None, [], {}])
def test_falsy_contains_personal_passes(value: Any) -> None:
    out = sanitize_for_llm({"contains_personal": value, "body": "hello"})
    assert verify(out)


def test_contains_personal_missing_is_fine() -> None:
    out = sanitize_for_llm({"body": "hello"})
    assert verify(out)


def test_sanitizer_refusal_not_caught_by_valueerror() -> None:
    """C3 regression: SanitizerRefusal must NOT be a subclass of ValueError.
    A generic `except ValueError` in a builder must not silently swallow refusals.
    """
    with pytest.raises(SanitizerRefusal):
        try:
            sanitize_for_llm({"contains_personal": True})
        except ValueError:  # noqa: BLE001 - deliberately testing this doesn't catch
            pytest.fail("SanitizerRefusal was swallowed by except ValueError")


def test_non_dict_rejected() -> None:
    with pytest.raises(TypeError):
        sanitize_for_llm("just a string")  # type: ignore[arg-type]


def test_returns_defensive_copy_of_data() -> None:
    """Mutating the input dict after sanitization should not change the wrapper."""
    src = {"a": 1}
    out = sanitize_for_llm(src)
    src["a"] = 999
    assert out.data["a"] == 1


def test_sanitized_payload_is_frozen() -> None:
    """Callers cannot silently mutate authenticator after construction."""
    out = sanitize_for_llm({"a": 1})
    with pytest.raises(Exception):  # FrozenInstanceError
        out.authenticator = "0" * 64  # type: ignore[misc]


def test_nested_mutation_after_verify_does_not_change_wrapper() -> None:
    """M-sanitizer-toctou-1 regression (A10 review).

    Before the fix, `_transform` was `dict(payload)` — a top-level shallow
    copy. A nested list or dict was still shared by reference with the
    caller. A caller could pass verify(), then mutate the shared nested
    container between verify and the LLM send, and the sent bytes would
    differ from the authenticated bytes.

    After the fix, `_transform` snapshots via `json.loads(_stable_json(...))`,
    so no mutable object is shared with the caller and this test's mutation
    cannot reach `out.data`.
    """
    inner = {"role": "SWE"}
    src = {"candidate": inner, "meta": [1, 2, 3]}
    out = sanitize_for_llm(src)
    assert verify(out) is True

    # Mutate through the still-held caller references. Using a plain scalar
    # rather than an @-shaped placeholder so this test file itself doesn't
    # trip the safety scanner in CI.
    inner["injected"] = "LEAKED_MARKER"
    src["meta"].append(999)  # type: ignore[union-attr]

    # Wrapper snapshot is unaffected — content the HMAC covers has not changed.
    assert "injected" not in out.data["candidate"]
    assert out.data["meta"] == [1, 2, 3]
    # Verify still passes; the caller's mutation could not desync send-vs-authenticated bytes.
    assert verify(out) is True


def test_send_to_llm_returns_data_on_verify_success() -> None:
    """Walkthrough #2 (A10 review): the send boundary exists and consumes
    SanitizedPayload. Verify passes, and the caller receives the snapshotted
    data. Phase B will replace the return with a real LLM client call."""
    from jscc.sanitizer import send_to_llm

    out = sanitize_for_llm({"jd_summary": "SWE at ExampleCo"})
    result = send_to_llm(out)
    assert result == out.data
    assert result["jd_summary"] == "SWE at ExampleCo"


def test_send_to_llm_refuses_bare_dict() -> None:
    """Walkthrough #2: the runtime type check must catch a bare dict cast
    past mypy — the type annotation is not enough on its own."""
    from typing import cast
    from jscc.sanitizer import LLMSendError, send_to_llm

    with pytest.raises(LLMSendError):
        send_to_llm(cast(SanitizedPayload, {"role": "SWE"}))


def test_send_to_llm_refuses_forged_payload() -> None:
    """Walkthrough #2: a hand-constructed SanitizedPayload with a bogus
    authenticator must not send — verify must fire at the boundary."""
    from jscc.sanitizer import LLMSendError, send_to_llm

    forged = SanitizedPayload(
        data={"role": "SWE"},
        sanitized_at="2026-08-28T12:00:00+00:00",
        authenticator="0" * 64,
    )
    with pytest.raises(LLMSendError):
        send_to_llm(forged)
