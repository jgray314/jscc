from __future__ import annotations

from datetime import datetime

import pytest

from jscc.sanitizer import (
    MARKER_KEY,
    SanitizerRefusal,
    is_sanitized,
    sanitize_for_llm,
)


def test_marker_present_on_roundtrip() -> None:
    out = sanitize_for_llm({"role": "SWE", "company": "ExampleCo"})
    assert MARKER_KEY in out
    # ISO-8601 UTC parses cleanly
    parsed = datetime.fromisoformat(out[MARKER_KEY])
    assert parsed.tzinfo is not None


def test_passthrough_preserves_original_fields() -> None:
    src = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    out = sanitize_for_llm(src)
    for k, v in src.items():
        assert out[k] == v


def test_returns_shallow_copy() -> None:
    src = {"a": 1}
    out = sanitize_for_llm(src)
    assert out is not src
    assert MARKER_KEY not in src  # caller's dict untouched


def test_refuses_contains_personal_true() -> None:
    with pytest.raises(SanitizerRefusal, match="contains_personal"):
        sanitize_for_llm({"contains_personal": True, "body": "..."})


def test_contains_personal_false_is_fine() -> None:
    out = sanitize_for_llm({"contains_personal": False, "body": "hello"})
    assert MARKER_KEY in out


def test_contains_personal_missing_is_fine() -> None:
    out = sanitize_for_llm({"body": "hello"})
    assert MARKER_KEY in out


def test_non_dict_rejected() -> None:
    with pytest.raises(TypeError):
        sanitize_for_llm("just a string")  # type: ignore[arg-type]


def test_is_sanitized_checks() -> None:
    assert not is_sanitized({"a": 1})
    assert is_sanitized(sanitize_for_llm({"a": 1}))
    assert not is_sanitized("not a dict")  # type: ignore[arg-type]
