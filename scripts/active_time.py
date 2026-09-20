"""Estimate active working time from git commit timestamps, with the bias stated.

Sums the gap between each pair of consecutive commits, dropping any gap longer
than a cap (default 4 hours) as "not working". This is a proxy, and it is wrong
in both directions at once:

- Upper bias: a gap under the cap still contains meals, interrupts and context
  switches. Broken-up work therefore reads high.
- Lower bias: the first commit of each cluster has no measured lead-in, and a
  batched commit attributes all the work behind it to a single gap. Work that
  never reaches a commit (planning docs outside the repo, manual capture rounds
  done in a chat window) is not seen at all.

Because those caveats are what make the number honest, `main` prints them every
time. A figure quoted without them is not the figure this script produces.

The feature / gate split is a keyword match on commit subjects, not a judgment
about the work. Treat it as roughly +/- 10 points.

Not part of CI. Usage:
    uv run python scripts/active_time.py
    uv run python scripts/active_time.py --cap-hours 2 --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

GATE = "gate/hardening/docs"
FEATURE = "feature/eval/prompt"

# Subjects matching any of these count as gate, hardening or upkeep work. Kept as
# one constant so the classification is inspectable and changeable in one place.
_GATE_RE = re.compile(
    r"gate|hardening|closure|hotfix|^ci:|scanner|review|prose pass|backlog sweep"
    r"|sweep|ruff|honest|compact|^docs:|polish|\bfix\b|\bw-?\d+\b",
    re.IGNORECASE,
)

CAVEATS = (
    "Upper bound: gaps under the cap still include interrupts, meals and context switches.",
    "Lower bound: the first commit of each cluster has no measured lead-in, and a batched "
    "commit hides the work behind it, so only cluster totals are usable, not per-slice times.",
    "Not captured: planning and review time outside this repo, and manual capture rounds "
    "done in a chat window, until the recording is committed.",
    "Classifier: the feature/gate split is a keyword match on commit subjects, roughly "
    "+/- 10 points.",
)


@dataclass(frozen=True)
class Commit:
    sha: str
    when: datetime
    subject: str


@dataclass(frozen=True)
class ActiveTime:
    commits: int
    clusters: int
    counted_gaps: int
    dropped_gaps: int
    total: timedelta
    by_category: dict[str, timedelta]
    by_day: dict[str, timedelta]
    gap_buckets: dict[str, int]


def classify(subject: str) -> str:
    return GATE if _GATE_RE.search(subject) else FEATURE


def analyze(commits: list[Commit], cap: timedelta = timedelta(hours=4)) -> ActiveTime:
    """Sum gaps at or under `cap`. The time is credited to the commit that ends the gap."""
    ordered = sorted(commits, key=lambda c: c.when)
    total = timedelta()
    by_category: dict[str, timedelta] = {FEATURE: timedelta(), GATE: timedelta()}
    by_day: dict[str, timedelta] = {}
    buckets: Counter[str] = Counter()
    counted = dropped = 0
    clusters = 1 if ordered else 0

    for prev, cur in zip(ordered, ordered[1:], strict=False):
        gap = cur.when - prev.when
        if gap > cap:
            dropped += 1
            clusters += 1
            continue
        counted += 1
        total += gap
        by_category[classify(cur.subject)] += gap
        day = cur.when.strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, timedelta()) + gap
        buckets[_bucket(gap)] += 1

    return ActiveTime(
        commits=len(ordered),
        clusters=clusters,
        counted_gaps=counted,
        dropped_gaps=dropped,
        total=total,
        by_category=by_category,
        by_day=dict(sorted(by_day.items())),
        gap_buckets={k: buckets.get(k, 0) for k in ("<=30m", "30m-2h", "2h-cap")},
    )


def _bucket(gap: timedelta) -> str:
    if gap <= timedelta(minutes=30):
        return "<=30m"
    if gap <= timedelta(hours=2):
        return "30m-2h"
    return "2h-cap"


def parse_log(text: str) -> list[Commit]:
    """Parse `git log --format=%h|%aI|%s` output."""
    commits = []
    for line in text.splitlines():
        if not line.strip():
            continue
        sha, when, subject = line.split("|", 2)
        commits.append(Commit(sha, datetime.fromisoformat(when), subject))
    return commits


def read_git_log(repo: Path) -> list[Commit]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--reverse", "--format=%h|%aI|%s"],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return parse_log(out.stdout)


def _hours(td: timedelta) -> float:
    return round(td.total_seconds() / 3600, 1)


def format_report(result: ActiveTime, cap: timedelta) -> str:
    total_h = _hours(result.total)
    lines = [
        f"Commits: {result.commits}    work clusters: {result.clusters}    "
        f"(a gap over {_hours(cap)}h starts a new cluster)",
        f"Active time, upper-bound proxy: {total_h}h",
    ]
    for name, td in result.by_category.items():
        share = round(100 * td / result.total) if result.total else 0
        lines.append(f"  {name:<22} {_hours(td):>5}h  ({share}%)")
    buckets = "  ".join(f"{k}: {v}" for k, v in result.gap_buckets.items())
    lines.append(f"Counted gaps by size: {buckets}    dropped over cap: {result.dropped_gaps}")
    lines.append("By day:")
    lines.extend(f"  {day}  {_hours(td):>5}h" for day, td in result.by_day.items())
    lines.append("")
    lines.append("Read before quoting any number above:")
    lines.extend(f"  - {c}" for c in CAVEATS)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--repo", type=Path, default=REPO_ROOT, help="repo to read (default: this one)"
    )
    parser.add_argument("--cap-hours", type=float, default=4.0, help="drop gaps longer than this")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    cap = timedelta(hours=args.cap_hours)
    result = analyze(read_git_log(args.repo), cap)
    if args.json:
        payload = {
            "commits": result.commits,
            "clusters": result.clusters,
            "active_hours_upper_bound": _hours(result.total),
            "by_category_hours": {k: _hours(v) for k, v in result.by_category.items()},
            "by_day_hours": {k: _hours(v) for k, v in result.by_day.items()},
            "gap_buckets": result.gap_buckets,
            "dropped_gaps": result.dropped_gaps,
            "caveats": list(CAVEATS),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(result, cap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
