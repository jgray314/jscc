"""`@instrumented` — the D5 call-site wrapper every LLM call must go through.

Lands ahead of any real LLM call (Phase B ships the first one, B2) on
purpose: D5 treats cost/latency capture as Phase A foundation so nothing
from B2 onward can slip through uninstrumented.

The wrapped function owns the actual LLM call and reports usage back via
`LLMResult`; the decorator owns timing, hashing, and persistence. Splitting
it this way means the decorator never needs to know which client library
or model family produced the response.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar

from .models import LLMCallRecord, _now
from .storage import record_llm_call

T = TypeVar("T")


@dataclass(frozen=True)
class LLMResult:
    """What an instrumented function must return: the caller's payload plus
    the usage figures needed for the ledger row."""

    output: Any
    input_tokens: int
    output_tokens: int
    cost_usd: float


def instrumented(feature: str) -> Callable[[Callable[..., LLMResult]], Callable[..., Any]]:
    """Decorate an LLM-call function: `(conn, model, prompt, *a, **kw) -> LLMResult`.

    Records one `llm_calls` row per invocation (call_id, feature, model,
    prompt_hash, tokens, cost, latency, ts) and returns `result.output` —
    callers see the same return shape they'd get from an uninstrumented call.
    """

    def decorator(fn: Callable[..., LLMResult]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(
            conn: sqlite3.Connection, model: str, prompt: str, *args: Any, **kwargs: Any
        ) -> Any:
            start = time.perf_counter()
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            try:
                result = fn(conn, model, prompt, *args, **kwargs)
            except Exception as e:
                # `fn` owns the actual network call; if it raises, tokens may
                # already be billed on the provider's side (a connection reset
                # or read-timeout after the response started generating) but
                # there is no `LLMResult` to read real figures from.
                # Previously this wrote nothing
                # at all, so the ledger -- whose stated purpose is cost
                # transparency -- had no record an attempt was even made.
                # Record what's known (zeroed usage, the error) and re-raise
                # unchanged; callers' existing except-and-route-to-DLQ
                # behavior is untouched.
                latency_ms = (time.perf_counter() - start) * 1000
                record_llm_call(
                    conn,
                    LLMCallRecord(
                        feature=feature,
                        model=model,
                        prompt_hash=prompt_hash,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=0.0,
                        latency_ms=latency_ms,
                        ts=_now(),
                        error=f"{type(e).__name__}: {e}",
                    ),
                )
                raise
            latency_ms = (time.perf_counter() - start) * 1000

            record = LLMCallRecord(
                feature=feature,
                model=model,
                prompt_hash=prompt_hash,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=result.cost_usd,
                latency_ms=latency_ms,
                ts=_now(),
            )
            record_llm_call(conn, record)
            return result.output

        return wrapper

    return decorator
