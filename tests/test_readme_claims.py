"""The README's test count, kept honest by the suite it counts.

This number has gone stale four times: a slice adds tests, the README keeps the
old figure, and nobody notices because nothing reads it. It is a small lie in
the one document a cold reader trusts most, sitting two lines from the claim
that the sample output reproduces exactly -- and a repo whose pitch is honest
engineering cannot afford a number that is casually wrong.

Hand-maintained facts drift; this makes the drift fail the build instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"
_COUNT_RE = re.compile(r"(\d+) pytest cases\.")


def test_readme_reports_the_real_test_count(request: pytest.FixtureRequest) -> None:
    """Skipped on a filtered run, where the collected count is a subset by design.

    The guard is the fiddly half: `-k`, `-m`, and naming specific files all
    collect fewer tests than exist, and asserting against that would fail for a
    reason that has nothing to do with the README. A skip there is honest; the
    full run -- which is what CI does -- is where the claim has to hold.
    """
    config = request.config
    if config.option.keyword or config.option.markexpr:
        pytest.skip("filtered run; collected count is a subset")
    if getattr(config.option, "lf", False) or getattr(config.option, "failedfirst", False):
        pytest.skip("partial-rerun mode; collected count is a subset")
    args = [a for a in config.args if not a.startswith("-")]
    if any(Path(a.split("::")[0]).is_file() for a in args):
        pytest.skip("specific files named; collected count is a subset")

    collected = request.session.testscollected

    match = _COUNT_RE.search(README.read_text(encoding="utf-8"))
    assert match, "README no longer states a test count in the expected form"
    claimed = int(match.group(1))
    assert claimed == collected, (
        f"README claims {claimed} pytest cases; the suite collected {collected}. "
        "Update both places in README.md (the repo-layout line and the Status "
        "line) rather than only the one that failed."
    )


def test_the_two_readme_counts_agree() -> None:
    """The count appears twice. Fixing one and not the other is the near miss."""
    text = README.read_text(encoding="utf-8")
    layout = re.search(r"pytest suite \((\d+) tests\)", text)
    status = _COUNT_RE.search(text)
    assert layout and status, "one of the two README test-count lines is missing"
    assert layout.group(1) == status.group(1)
