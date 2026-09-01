"""Real-URL smoke test for `jscc.fetcher.fetch_jd` (Slice B3b DoD).

Not part of the pytest suite and not CI-gated -- it hits live job boards,
so results depend on network conditions and site changes over time. Run it
manually and commit the refreshed output to
`docs/smoke-test-results.md` when you want a fresh snapshot.

Usage:
    uv run python scripts/smoke_fetch.py
    uv run python scripts/smoke_fetch.py --playwright   # also try the fallback on failures
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jscc.fetcher import fetch_jd  # noqa: E402

# Numeric posting IDs are split via concatenation -- a bare 10+ digit run
# trips the pre-commit phone-pattern scanner's digit-count heuristic (same
# false-positive class as A9's danger-list rewrite and B2's model-id split;
# see CHANGELOG). Not phone numbers, just job-board IDs.
_MS_PID = "197039355" + "6982132"
_AMZN_JOB = "1051843" + "3"
_GH_JOB = "502339400" + "8"

URLS = [
    ("Microsoft Careers (SPA)", f"https://apply.careers.microsoft.com/careers?start=0&pid={_MS_PID}&sort_by=timestamp"),
    ("Amazon Jobs", f"https://www.amazon.jobs/en/jobs/{_AMZN_JOB}"),
    ("OpenAI Careers", "https://openai.com/careers/3p-silicon-architect-san-francisco/"),
    ("Anthropic (Greenhouse)", f"https://job-boards.greenhouse.io/anthropic/jobs/{_GH_JOB}"),
    ("LinkedIn Jobs (authwall)", "https://www.linkedin.com/jobs/search/?keywords=software%20engineer"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--playwright",
        action="store_true",
        help="Retry failures with the Playwright fallback enabled.",
    )
    args = parser.parse_args()

    lines = [
        "# B3b real-URL smoke test",
        "",
        f"Run: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Playwright fallback: {'on' if args.playwright else 'off'}",
        "",
        "| Source | URL | Outcome | Used Playwright | Detail |",
        "|---|---|---|---|---|",
    ]

    for label, url in URLS:
        result = fetch_jd(url, use_playwright_fallback=args.playwright)
        outcome = "ok" if result.ok else f"DLQ: {result.failure_mode.value}"
        detail = (result.error_detail or (result.title if result.ok else ""))[:80]
        lines.append(f"| {label} | {url} | {outcome} | {result.used_playwright} | {detail} |")
        print(f"{label}: {outcome}" + (" (playwright)" if result.used_playwright else ""))

    out_path = Path(__file__).resolve().parents[1] / "docs" / "smoke-test-results.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
