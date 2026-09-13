"""Shared JSON serialization fallback for storage and the sanitizer.

Gate finding L-json-default-sanitizer-1: `jscc/sanitizer.py`'s `_stable_json`
used to fall back to `default=str`, silently stringifying any type it didn't
recognize -- while `jscc/storage.py`'s `_dump_json` raised on the same case.
An unexpected type reaching either one (a stray `bytes` object, a custom
class that slipped past a schema) is a real bug worth surfacing loudly, not
a value worth a best-effort `str()` -- especially inside the sanitizer, where
the "stringified" form feeds the canonical snapshot redaction runs against
and the HMAC input, not just a log line. One definition, two call sites, so
the two egress points can't quietly disagree about what counts as a
serialization bug the way personal_data.py exists so M3/M5 can't disagree
about what counts as personal data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


def json_default(obj: Any) -> Any:
    """Fallback serializer for `json.dumps(..., default=json_default)`.

    Handles the value types Phase B/C are likely to embed in a payload
    (datetimes, sets, pydantic models, enums) without silently swallowing
    types the schema hasn't decided about.
    """
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=UTC)
        return obj.astimezone(UTC).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        # Sort for determinism; if elements aren't comparable, TypeError bubbles
        # up as a real schema-design signal rather than being silently masked.
        return sorted(obj, key=repr)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON-serializable")
