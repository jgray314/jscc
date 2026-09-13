"""Pure functions for C3's cost/latency reporting layer.

D5's `@instrumented` decorator has landed one `llm_calls` row per LLM call
since Phase A; this module is what finally reads that ledger back out. No DB
access here -- callers pass in the rows already loaded from storage.
Testable in isolation; the CLI is a thin wrapper, same shape as
`report.py`'s pipeline reporting.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from .llm_client import UnknownModelPricingError, rates_for
from .models import LLMCallRecord


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. `pct` in [0, 100]. Empty input is a caller error."""
    if not values:
        raise ValueError("percentile of an empty sequence is undefined")
    ordered = sorted(values)
    rank = math.ceil(pct / 100 * len(ordered)) - 1
    rank = max(0, min(rank, len(ordered) - 1))
    return ordered[rank]


class FeatureCostSummary(BaseModel):
    feature: str
    calls: int
    total_cost_usd: float
    avg_cost_usd: float
    p50_latency_ms: float
    p95_latency_ms: float


class CostRegressionFinding(BaseModel):
    """One ledger row whose recorded cost no longer matches what today's
    published rate (`rates_for`) computes for its token counts -- the same
    discrepancy shape B12 caught by hand (a stale rate under-recording every
    call by a fixed factor, found only on manual review). A call whose model
    has no rate on file (stub clients, test fixtures) is skipped rather than
    flagged -- it was never priced against a real rate to drift from."""

    call_id: str
    feature: str
    model: str
    recorded_cost_usd: float
    expected_cost_usd: float


# 1% covers float rounding noise, not a real drift. The absolute floor keeps
# a near-zero-cost call (a handful of tokens) from tripping the relative
# tolerance on noise alone.
_REGRESSION_RELATIVE_TOLERANCE = 0.01
_REGRESSION_ABSOLUTE_FLOOR_USD = 0.0005


def summarize_costs(calls: list[LLMCallRecord]) -> list[FeatureCostSummary]:
    """One row per feature (D5's cost-isolation label), sorted by feature name."""
    by_feature: dict[str, list[LLMCallRecord]] = {}
    for call in calls:
        by_feature.setdefault(call.feature, []).append(call)

    summaries: list[FeatureCostSummary] = []
    for feature, feature_calls in sorted(by_feature.items()):
        latencies = [c.latency_ms for c in feature_calls]
        total_cost = sum(c.cost_usd for c in feature_calls)
        summaries.append(
            FeatureCostSummary(
                feature=feature,
                calls=len(feature_calls),
                total_cost_usd=total_cost,
                avg_cost_usd=total_cost / len(feature_calls),
                p50_latency_ms=percentile(latencies, 50),
                p95_latency_ms=percentile(latencies, 95),
            )
        )
    return summaries


def find_cost_regressions(calls: list[LLMCallRecord]) -> list[CostRegressionFinding]:
    """Flag ledger rows whose recorded cost no longer matches the currently
    published rate for their model -- catches a stale/wrong rate the same
    way B12 was found, without needing to re-derive it by hand every time.
    """
    findings: list[CostRegressionFinding] = []
    for call in calls:
        try:
            input_rate, output_rate = rates_for(call.model)
        except UnknownModelPricingError:
            continue
        expected = (call.input_tokens / 1_000_000) * input_rate + (
            call.output_tokens / 1_000_000
        ) * output_rate
        tolerance = max(_REGRESSION_ABSOLUTE_FLOOR_USD, expected * _REGRESSION_RELATIVE_TOLERANCE)
        if abs(expected - call.cost_usd) > tolerance:
            findings.append(
                CostRegressionFinding(
                    call_id=call.id,
                    feature=call.feature,
                    model=call.model,
                    recorded_cost_usd=call.cost_usd,
                    expected_cost_usd=expected,
                )
            )
    return findings


def format_cost_report(
    summaries: list[FeatureCostSummary], regressions: list[CostRegressionFinding]
) -> str:
    """Render a text report suitable for a CLI or a scheduled digest."""
    if not summaries:
        return "no LLM calls recorded yet"

    lines: list[str] = []
    lines.append(
        f"{'feature':<20}{'calls':>8}{'total_usd':>12}{'avg_usd':>10}{'p50_ms':>10}{'p95_ms':>10}"
    )
    for s in summaries:
        lines.append(
            f"{s.feature:<20}{s.calls:>8}{s.total_cost_usd:>12.4f}{s.avg_cost_usd:>10.4f}"
            f"{s.p50_latency_ms:>10.1f}{s.p95_latency_ms:>10.1f}"
        )

    lines.append("")
    lines.append(f"Cost regressions ({len(regressions)})")
    if not regressions:
        lines.append("  (none -- every priced call matches its model's current published rate)")
    else:
        for r in regressions:
            lines.append(
                f"  [{r.feature}] call {r.call_id} model={r.model} "
                f"recorded=${r.recorded_cost_usd:.4f} expected=${r.expected_cost_usd:.4f} "
                "-- rate mismatch, check _MODEL_RATES_USD_PER_MTOK"
            )
    return "\n".join(lines)
