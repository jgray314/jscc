from __future__ import annotations

import pytest

from jscc.cost_report import (
    find_cost_regressions,
    format_cost_report,
    percentile,
    summarize_costs,
)
from jscc.llm_client import EXTRACTION_MODEL
from jscc.models import LLMCallRecord


def _call(
    *,
    feature: str = "extraction",
    model: str = EXTRACTION_MODEL,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: float = 100.0,
    error: str | None = None,
) -> LLMCallRecord:
    return LLMCallRecord(
        feature=feature,
        model=model,
        prompt_hash="hash",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        error=error,
    )


def test_percentile_of_empty_sequence_raises() -> None:
    with pytest.raises(ValueError):
        percentile([], 50)


def test_percentile_single_value() -> None:
    assert percentile([42.0], 95) == 42.0


def test_percentile_p50_and_p95_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]  # 1..100
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0


def test_summarize_costs_groups_by_feature_and_computes_percentiles() -> None:
    calls = [
        _call(feature="extraction", cost_usd=0.10, latency_ms=100.0),
        _call(feature="extraction", cost_usd=0.20, latency_ms=200.0),
        _call(feature="scoring", cost_usd=0.50, latency_ms=300.0),
    ]
    summaries = {s.feature: s for s in summarize_costs(calls)}

    assert set(summaries) == {"extraction", "scoring"}
    extraction = summaries["extraction"]
    assert extraction.calls == 2
    assert extraction.total_cost_usd == pytest.approx(0.30)
    assert extraction.avg_cost_usd == pytest.approx(0.15)
    assert extraction.p50_latency_ms == 100.0  # nearest-rank of [100, 200]
    assert extraction.p95_latency_ms == 200.0

    scoring = summaries["scoring"]
    assert scoring.calls == 1
    assert scoring.total_cost_usd == pytest.approx(0.50)


def test_summarize_costs_excludes_failure_marker_rows() -> None:
    """Gate finding G3: a row with `error` set carries zeroed usage by
    construction (the call raised before real figures came back) -- folding
    it into an average would understate cost/latency for calls that actually
    completed, the same reason an unpriced model is skipped in
    `find_cost_regressions`."""
    calls = [
        _call(feature="scoring", cost_usd=0.50, latency_ms=300.0),
        _call(feature="scoring", cost_usd=0.0, latency_ms=5.0, error="ConnectionError: reset"),
    ]
    summaries = {s.feature: s for s in summarize_costs(calls)}
    assert summaries["scoring"].calls == 1
    assert summaries["scoring"].total_cost_usd == pytest.approx(0.50)


def test_a_feature_whose_only_calls_failed_is_still_reported() -> None:
    calls = [_call(feature="scoring", error="TimeoutError: read timed out")]
    [summary] = summarize_costs(calls)
    assert (summary.feature, summary.calls, summary.failed) == ("scoring", 0, 1)
    report = format_cost_report([summary], [])
    assert "no LLM calls recorded yet" not in report
    assert "1 call(s) failed mid-request" in report


def test_failed_calls_are_counted_alongside_completed_ones() -> None:
    calls = [
        _call(feature="scoring", cost_usd=0.50, latency_ms=300.0),
        _call(feature="scoring", error="ConnectionError: reset"),
    ]
    [summary] = summarize_costs(calls)
    assert (summary.calls, summary.failed) == (1, 1)


def test_find_cost_regressions_skips_failure_marker_rows() -> None:
    call = _call(model=EXTRACTION_MODEL, cost_usd=0.0, error="ConnectionError: reset")
    assert find_cost_regressions([call]) == []


def test_find_cost_regressions_skips_calls_with_no_published_rate() -> None:
    calls = [_call(model="claude-does-not-exist", cost_usd=999.0)]
    assert find_cost_regressions(calls) == []


def test_find_cost_regressions_passes_a_correctly_priced_call() -> None:
    # EXTRACTION_MODEL is $1.00/$5.00 per MTok (input, output).
    call = _call(
        model=EXTRACTION_MODEL,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cost_usd=6.00,
    )
    assert find_cost_regressions([call]) == []


def test_find_cost_regressions_flags_a_stale_rate_like_b12() -> None:
    # B12: the ledger priced a call under the currently-published rate.
    # Recorded cost is 20% under what today's rate would compute.
    call = _call(
        model=EXTRACTION_MODEL,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cost_usd=4.80,  # expected is 6.00 at current rates
    )
    findings = find_cost_regressions([call])
    assert len(findings) == 1
    finding = findings[0]
    assert finding.model == EXTRACTION_MODEL
    assert finding.recorded_cost_usd == pytest.approx(4.80)
    assert finding.expected_cost_usd == pytest.approx(6.00)


def test_find_cost_regressions_tolerates_float_rounding_noise() -> None:
    call = _call(
        model=EXTRACTION_MODEL,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cost_usd=6.0001,  # a hair off, not a real drift
    )
    assert find_cost_regressions([call]) == []


def test_format_cost_report_empty() -> None:
    assert format_cost_report([], []) == "no LLM calls recorded yet"


def test_format_cost_report_includes_feature_and_regression_sections() -> None:
    calls = [_call(model=EXTRACTION_MODEL, input_tokens=1_000_000, cost_usd=0.50)]
    summaries = summarize_costs(calls)
    regressions = find_cost_regressions(calls)  # under-priced vs. published rate
    report = format_cost_report(summaries, regressions)
    assert "extraction" in report
    assert "Cost regressions (1)" in report
    assert "rate mismatch" in report
