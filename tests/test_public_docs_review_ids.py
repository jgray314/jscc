"""Review-finding ids stay in the gate record, not in the code and docs they once fixed.

The Phase D gate scrubbed finding ids out of comments and docs; the Phase E gate
found a different set back, and the full-project gate another. A finding id (a
severity letter and number, a walkthrough number, a pass name) means nothing to a
reader without the review document, and the review document names the code it
fixed, so the reference runs the wrong way. Each id that survives is a comment
that decays: the id is renumbered, the finding is merged into another, and the
comment keeps pointing.

The gate record itself (CHANGELOG.md and docs/gate-reviews.md) is where these
ids belong and is exempt. Everything else shares test_public_docs.py's file walk
and denylist. Mitigation ids from the design principles (M1 to M7 under D7),
slice names (A1, C2b), threat ids (T1 to T13), decision ids (D1 to D10) and
ADR numbers are not review ids and are not matched.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_public_docs import FORBIDDEN, public_files

GATE_RECORD = {Path("CHANGELOG.md"), Path("docs/gate-reviews.md")}

# Fixture data that parses commit subjects; the shapes there are the input under test.
FIXTURE_EXEMPT = {Path("tests/test_active_time.py")}

# A pointer into the gate record's own numbered findings ("gate-reviews.md finding 7")
# is a link, not a leaked id.
POINTER = "gate-reviews"

REVIEW_ID_SHAPES = {
    "a gate finding reference": re.compile(
        r"(?i:\bgate[- ]findings?),? (?:[A-Z]{1,2}-?[\w-]*\d|#?\d)"
    ),
    "a rerun-gate reference": re.compile(r"(?i)\brerun-gate\b"),
    "a severity letter and number joined by a dash": re.compile(r"\b[CHMLWG]-\d{1,2}\b"),
    "a lettered finding id": re.compile(r"\b[A-Z]-[A-Z]\d+\b"),
    "a phase-scoped finding id": re.compile(r"\bL\d+-\d+\b"),
    "a walkthrough or gate-number id": re.compile(r"\b(?:WD-?|W|G)\d{1,2}\b"),
    "a slashed list of severity ids": re.compile(r"\b[CH]\d(?:/[CHM]\d)+\b"),
    "a numbered finding": re.compile(r"(?i)\bfinding #?[A-Z]?-?\d+\b"),
    "a review pass by ordinal": re.compile(
        r"(?i)\b(?:first|second|third|fourth|fifth|1st|2nd|3rd) (?:cold )?(?:rerun|pass)\b"
    ),
    "a review pass by number": re.compile(r"(?i)\bpass (?:one|two|three|four|[1-4])\b(?!/)"),
}


def shapes_in(line: str) -> list[str]:
    found = {**FORBIDDEN, **REVIEW_ID_SHAPES}
    hits = [what for what, pattern in found.items() if pattern.search(line)]
    if POINTER in line:
        hits = [h for h in hits if h != "a numbered finding"]
    return hits


def test_review_ids_appear_only_in_the_gate_record() -> None:
    offenders = []
    for rel, path in public_files():
        if rel in GATE_RECORD or rel in FIXTURE_EXEMPT:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            offenders += [f"{rel.as_posix()}:{lineno}: {what}" for what in shapes_in(line)]
    assert not offenders, (
        "review-finding ids outside CHANGELOG.md and docs/gate-reviews.md "
        "(say what the code does, not which finding it answered):\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "text",
    [
        "see finding 7",
        "closed the second pass",
        "gate finding M-6",
        "rerun-gate",
        "H-6",
        "M-12",
        "L1-12",
        "G-D1",
        "W15",
        "WD-6",
        "G4",
        "C4/C5/C6",
        "Phase C->D pass 2",
    ],
)
def test_each_review_id_shape_is_caught(text: str) -> None:
    assert shapes_in(text), text


@pytest.mark.parametrize(
    "text",
    [
        "D7 M3",
        "(M1)",
        "M3/M5",
        "Slice C2b",
        "T13",
        "ADR-005",
        "L6-L7 equivalent",
        "D10",
        "the C1 range",
        "which pass 2/2 with the check",
        "from the code and the gate findings",
        "see `docs/gate-reviews.md` finding 7",
    ],
)
def test_text_that_is_not_a_review_id_is_not_caught(text: str) -> None:
    assert not shapes_in(text), text


def test_the_exempt_files_exist_so_an_exemption_cannot_go_stale() -> None:
    root = Path(__file__).resolve().parents[1]
    assert all((root / rel).is_file() for rel in GATE_RECORD | FIXTURE_EXEMPT)
