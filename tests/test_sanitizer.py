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
