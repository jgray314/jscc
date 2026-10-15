"""CHANGELOG.md stays inside a stated size, and its header names the phase the project is in.

A standing step ("fold the older phases at each gate") was written down and the file
still outgrew review five times, because a step nobody is forced to run is a
suggestion. This is the forcing function: the file has a budget, and going over it
fails the build, which is the moment to fold a phase into a summary.

The header once said Phase F was "now closing" for days after Phase F closed. The
phase status has one source, the phase table in docs/technical-reference.md; the
header is checked against it rather than against a second hand-kept sentence.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_docs_claims import all_phases_closed, last_phase_letter

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

# Current size is about 355 lines and 53 KB, after Phase B was folded. The budget leaves room for a phase's worth
# of new entries and no more. Raise it only in a commit that says why the fold was
# not the right answer; the lines are long paragraphs, so the byte budget is the
# one that tracks reading time.
LINE_BUDGET = 380
BYTE_BUDGET = 58_000

_HEADER = re.compile(r"Phase ([A-Z]), the last phase, is (closed|open)")


def test_the_changelog_stays_inside_its_line_and_byte_budget() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    lines = len(text.splitlines())
    size = len(text.encode("utf-8"))
    assert lines <= LINE_BUDGET, (
        f"CHANGELOG.md is {lines} lines, budget {LINE_BUDGET}: fold the oldest "
        "unsummarized phase into a summary instead of raising the budget"
    )
    assert size <= BYTE_BUDGET, (
        f"CHANGELOG.md is {size} bytes, budget {BYTE_BUDGET}: fold the oldest "
        "unsummarized phase into a summary instead of raising the budget"
    )


def header_problems(header: str) -> list[str]:
    match = _HEADER.search(" ".join(header.split()))
    if not match:
        return ["the header does not say 'Phase X, the last phase, is closed|open'"]
    letter, state = match.groups()
    problems = []
    if letter != last_phase_letter():
        problems.append(f"names Phase {letter}; the phase table's last is {last_phase_letter()}")
    if (state == "closed") != all_phases_closed():
        problems.append(f"says the last phase is {state}; the phase table disagrees")
    return problems


def test_the_header_names_the_last_phase_and_whether_it_is_closed() -> None:
    header = CHANGELOG.read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert header_problems(header) == []


def test_a_header_that_went_stale_is_reported() -> None:
    assert header_problems("Phase F, the phase now closing, keeps its reasoning in full.")
    wrong_letter = "Phase C, the last phase, is closed."
    assert any("names Phase C" in p for p in header_problems(wrong_letter))
    open_but_done = f"Phase {last_phase_letter()}, the last phase, is open."
    assert any("disagrees" in p for p in header_problems(open_but_done))
