"""Pure functions for pipeline reporting: funnel counts and staleness detection.

No DB access here — callers pass in an `Application` list and a `StagesConfig`.
Testable in isolation; the CLI is a thin wrapper.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from .config import StagesConfig
from .models import Application


class StaleAlert(BaseModel):
    application_id: str
    title: str
    company: str
    stage: str
    days_since_last_interaction: int
    threshold_days: int

    @property
    def overdue_by_days(self) -> int:
        return self.days_since_last_interaction - self.threshold_days


def funnel_counts(
    apps: list[Application], stages_config: StagesConfig
) -> dict[str, int]:
    """Return per-stage counts in stages.yaml order, including zero-count stages.

    Apps whose `stage` isn't in the configured pipeline are omitted from the
    named counts and returned under the key `"__unknown__"` only if present.
    """
    counts: dict[str, int] = {s: 0 for s in stages_config.stages}
    unknown = 0
    for app in apps:
        if app.stage in counts:
            counts[app.stage] += 1
        else:
            unknown += 1
    if unknown:
        counts["__unknown__"] = unknown
    return counts


def _reference_time(app: Application) -> datetime | None:
    """The timestamp staleness is measured from: last_interaction_at when set,
    otherwise created_at (covers `identified`-stage apps with no interactions)."""
    return app.last_interaction_at or app.created_at


def detect_stale(
    apps: list[Application],
    stages_config: StagesConfig,
    *,
    now: datetime | None = None,
) -> list[StaleAlert]:
    """Return alerts for apps whose reference timestamp is older than their stage threshold.

    Alerts are sorted most-overdue first. Apps in unknown stages are skipped.
    """
    now = now or datetime.now(timezone.utc)
    alerts: list[StaleAlert] = []
    for app in apps:
        threshold = stages_config.staleness_thresholds_days.get(app.stage)
        if threshold is None:
            continue
        ref = _reference_time(app)
        if ref is None:
            continue
        # Coerce naive→UTC to match storage's contract (see ADR-002).
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        days = (now - ref).days
        # M6: a future reference timestamp means bad data (clock skew, corrupt
        # row, or a caller passing a --now in the past). Silent drop would
        # hide it — every alert path assumes the timestamp is in the past.
        if days < 0:
            raise ValueError(
                f"future reference timestamp for application {app.id!r}: "
                f"ref={ref.isoformat()} now={now.isoformat()}. "
                "Fix the row or the --now argument; do not silently drop."
            )
        if days >= threshold:
            alerts.append(
                StaleAlert(
                    application_id=app.id,
                    title=app.title,
                    company=app.company,
                    stage=app.stage,
                    days_since_last_interaction=days,
                    threshold_days=threshold,
                )
            )
    alerts.sort(key=lambda a: a.overdue_by_days, reverse=True)
    return alerts


def format_report(
    counts: dict[str, int], alerts: list[StaleAlert], stages_config: StagesConfig
) -> str:
    """Render a text report suitable for a CLI or a scheduled digest email."""
    lines: list[str] = []
    lines.append("Funnel")
    lines.append("------")
    stage_width = max((len(s) for s in stages_config.stages), default=10)
    total = 0
    for stage in stages_config.stages:
        n = counts.get(stage, 0)
        total += n
        lines.append(f"  {stage:<{stage_width}}  {n:>4}")
    if "__unknown__" in counts:
        lines.append(f"  {'(unknown stage)':<{stage_width}}  {counts['__unknown__']:>4}")
        total += counts["__unknown__"]
    lines.append(f"  {'(total)':<{stage_width}}  {total:>4}")
    lines.append("")
    lines.append(f"Stale alerts ({len(alerts)})")
    lines.append("-" * (14 + len(str(len(alerts)))))
    if not alerts:
        lines.append("  (none)")
    else:
        company_width = max(len(a.company) for a in alerts)
        for a in alerts:
            lines.append(
                f"  {a.stage:<{stage_width}}  {a.company:<{company_width}}  "
                f"{a.title}  "
                f"overdue by {a.overdue_by_days}d "
                f"(last interaction {a.days_since_last_interaction}d ago, threshold {a.threshold_days}d)"
            )
    return "\n".join(lines)
