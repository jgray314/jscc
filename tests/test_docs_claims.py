"""Every count and status the docs state about the project, with its source of truth.

The README's test count went stale four times, then "eighteen threats" sat beside a
table of thirteen, then "Six ADRs" beside seven files. Each was a hand-kept number
that nothing read. This is one registry: a claim names the file that states it, the
phrase that carries the number, and the function that computes the truth. A sweep
then fails on any count-shaped phrase in the public docs that no registry row
covers, so a new claim cannot appear unregistered.

History (the CHANGELOG's per-slice counts, round tables in evals/README.md) is not
a current claim and is not swept.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_WORDS = {
    w: i
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _number(token: str) -> int:
    token = token.lower()
    return int(token) if token.isdigit() else _WORDS[token]


# ---- sources of truth --------------------------------------------------------------


def adr_files() -> int:
    return len(list((ROOT / "decisions").glob("[0-9][0-9][0-9]-*.md")))


def threat_rows() -> int:
    return len(re.findall(r"(?m)^\| T\d+ \|", _read("docs/threat-model.md")))


def principle_headings() -> int:
    return len(re.findall(r"(?m)^## D\d+ ", _read("docs/design-principles.md")))


def gate_finding_headings() -> int:
    return len(re.findall(r"(?m)^### \d+\. ", _read("docs/gate-reviews.md")))


def phase_rows() -> list[tuple[str, bool, str]]:
    """The one status source: technical-reference.md's phase table.

    Returns (letter, closed, shipped cell) per lettered phase, in table order.
    """
    rows = re.findall(
        r"(?m)^\| \*\*Phase ([A-Z]) [^|]*\| ([^|]*)\|", _read("docs/technical-reference.md")
    )
    return [(letter, "losed" in cell, cell) for letter, cell in rows]


def last_phase_letter() -> str:
    return phase_rows()[-1][0]


def all_phases_closed() -> bool:
    return all(closed for _, closed, _ in phase_rows())


def final_close_date() -> str:
    match = re.search(r"[Cc]losed (\d{4}-\d{2}-\d{2})", phase_rows()[-1][2])
    assert match, "the last phase row no longer states a close date"
    return match.group(1)


def phase_count() -> int:
    return len(phase_rows())


def _published() -> dict[str, tuple[int, int]]:
    from tests.test_published_results import PUBLISHED

    return PUBLISHED


def figure(suite: str) -> str:
    passed, total = _published()[suite]
    return f"{passed}/{total} ({round(100 * passed / total)}%)"


# ---- the registry ------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    name: str
    file: str
    # Group 1 is the claimed value: a digit run, a number word, or a literal figure.
    pattern: str
    truth: Callable[[], object]
    # Compare the number of matches (a list the doc keeps) rather than a captured value.
    counted: bool = False
    # Highest number in a range such as "T1-T13": take the last group instead of the first.
    minimum_matches: int = 1


def _suite_figure(label: str, suite: str, file: str) -> Claim:
    return Claim(
        f"{suite} figure in {file}",
        file,
        rf"{label}[^\n]*?(\d+/\d+ \(\d+%\))",
        lambda s=suite: figure(s),
    )


REGISTRY: list[Claim] = [
    # test count: checked separately below, because it needs the live collection.
    Claim("ADR count (README)", "README.md", r"(\w+) ADRs and", adr_files),
    Claim(
        "ADR links (technical-reference)",
        "docs/technical-reference.md",
        r"(?m)^- \[ADR-\d{3} ",
        adr_files,
        counted=True,
    ),
    Claim("threat count (README)", "README.md", r"(\w+) threats, each", threat_rows),
    Claim("threat range (docs/README)", "docs/README.md", r"T1\W+T(\d+)\)", threat_rows),
    Claim(
        "design principle count (README)", "README.md", r"ADRs and (\w+) design", principle_headings
    ),
    Claim(
        "design principle count (design-principles)",
        "docs/design-principles.md",
        r"(\w+) decisions locked",
        principle_headings,
    ),
    Claim(
        "design principle count (docs/README)",
        "docs/README.md",
        r"the (\w+) locked decisions",
        principle_headings,
    ),
    Claim(
        "design principle range (docs/README)",
        "docs/README.md",
        r"D1\W+D(\d+)\)",
        principle_headings,
    ),
    Claim("phase count (README intro)", "README.md", r"in (\w+) phases", phase_count),
    Claim("phase count (README status)", "README.md", r"All (\w+) phases closed", phase_count),
    Claim(
        "phase count (how-i-built-this)",
        "docs/how-i-built-this.md",
        r"across (\w+) phases",
        phase_count,
    ),
    Claim(
        "final close date (README)",
        "README.md",
        r"the last on (\d{4}-\d{2}-\d{2})",
        final_close_date,
    ),
    Claim(
        "gate findings listed (docs/README)",
        "docs/README.md",
        r"(\w+) worked findings",
        gate_finding_headings,
    ),
    Claim(
        "gate findings listed (README)",
        "README.md",
        r"for (\w+) worked findings",
        gate_finding_headings,
    ),
    Claim(
        "design principle count (README, closing)",
        "README.md",
        r"The (\w+) design principles behind",
        principle_headings,
    ),
    Claim(
        "gate findings listed (lessons-learned)",
        "docs/lessons-learned.md",
        r"(\w+) worked findings",
        gate_finding_headings,
    ),
    Claim(
        "gate findings heading (gate-reviews)",
        "docs/gate-reviews.md",
        r"(?m)^## (\w+) findings worth reading",
        gate_finding_headings,
    ),
    # Eval headline figures. The table in evals/README.md is the published one;
    # test_published_results.py pins it to the recordings.
    *[
        Claim(
            f"{suite} row in evals/README.md",
            "evals/README.md",
            rf"(?m)^\| {suite} \| (\d+/\d+ \(\d+%\))",
            lambda s=suite: figure(s),
        )
        for suite in ("jd_extraction", "fit_scoring", "routing", "composition")
    ],
    Claim(
        "extraction headline (technical-reference)",
        "docs/technical-reference.md",
        r"\*\*Extraction: (\d+/\d+ \(\d+%\))\*\*",
        lambda: figure("jd_extraction"),
    ),
    Claim(
        "fit scoring headline (technical-reference)",
        "docs/technical-reference.md",
        r"\*\*Fit scoring: (\d+/\d+ \(\d+%\))\*\*",
        lambda: figure("fit_scoring"),
    ),
    Claim(
        "routing headline (technical-reference)",
        "docs/technical-reference.md",
        r"\*\*Routing: (\d+/\d+ \(\d+%\))\*\*",
        lambda: figure("routing"),
    ),
    Claim(
        "composition headline (technical-reference)",
        "docs/technical-reference.md",
        r"\*\*Composition: (\d+/\d+ \(\d+%\))\*\*",
        lambda: figure("composition"),
    ),
]

# The test count appears in these places; each is checked against the collection.
TEST_COUNT_CLAIMS = [
    ("README.md", r"(\d+) pytest cases\."),
    ("docs/technical-reference.md", r"pytest suite \((\d+) tests\)"),
    ("docs/technical-reference.md", r"(?m)^(\d+) pytest cases\."),
]


def _values(claim: Claim) -> list[str]:
    text = _read(claim.file)
    if claim.counted:
        return [str(len(re.findall(claim.pattern, text)))]
    return [m.group(1) for m in re.finditer(claim.pattern, text)]


@pytest.mark.parametrize("claim", REGISTRY, ids=lambda c: c.name)
def test_every_registered_claim_matches_its_source_of_truth(claim: Claim) -> None:
    values = _values(claim)
    assert values, f"{claim.file} no longer states '{claim.name}' in the registered form"
    truth = claim.truth()
    for value in values:
        got: object = value
        if isinstance(truth, int):
            got = _number(value)
        assert got == truth, f"{claim.file}: '{claim.name}' says {value}; the source says {truth}"


def test_the_test_count_claims_match_the_collection(request: pytest.FixtureRequest) -> None:
    """Skipped on a filtered run, where the collected count is a subset by design.

    `-k`, `-m`, `--lf` and naming files all collect fewer tests than exist, and
    asserting against that would fail for a reason that has nothing to do with the
    docs. CI runs the full suite, which is where the claim has to hold.
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
    for file, pattern in TEST_COUNT_CLAIMS:
        match = re.search(pattern, _read(file))
        assert match, f"{file} no longer states a test count as {pattern!r}"
        assert int(match.group(1)) == collected, (
            f"{file} claims {match.group(1)} tests; the suite collected {collected}. "
            "Update every place in TEST_COUNT_CLAIMS, not only the one that failed."
        )


# ---- sweep: a count-shaped phrase nobody registered fails ---------------------------

_SWEPT_FILES = [
    "README.md",
    "docs/README.md",
    "docs/design-principles.md",
    "docs/gate-reviews.md",
    "docs/how-i-built-this.md",
    "docs/lessons-learned.md",
    "docs/technical-reference.md",
    "docs/threat-model.md",
]
_COUNT_NOUNS = (
    r"(?:ADRs?|threats|design principles|phases|pytest cases|worked findings|findings worth)"
)
_NUMBER = r"(?:\d+|" + "|".join(_WORDS) + r")"
_SWEEP = re.compile(rf"(?i)\b{_NUMBER} (?:locked decisions|{_COUNT_NOUNS})\b")

# Count phrases that are deliberately not live claims, each with the reason.
FROZEN = {
    ("docs/technical-reference.md", "5 ADRs"): "what Phase A shipped; a historical row",
    ("docs/lessons-learned.md", "three phases"): "elapsed distance in a story, not a project count",
}


def _covered_spans() -> dict[str, list[tuple[int, int]]]:
    spans: dict[str, list[tuple[int, int]]] = {}
    for claim in REGISTRY:
        for m in re.finditer(claim.pattern, _read(claim.file)):
            spans.setdefault(claim.file, []).append(m.span())
    for file, pattern in TEST_COUNT_CLAIMS:
        for m in re.finditer(pattern, _read(file)):
            spans.setdefault(file, []).append(m.span())
    return spans


def test_a_count_shaped_phrase_without_a_registry_row_fails() -> None:
    covered = _covered_spans()
    unregistered = []
    for rel in _SWEPT_FILES:
        text = _read(rel)
        for m in _SWEEP.finditer(text):
            if (rel, m.group(0)) in FROZEN:
                continue
            inside = any(s <= m.start() and m.end() <= e for s, e in covered.get(rel, []))
            # A registered pattern captures the number word's neighborhood, not always
            # the noun; accept a registered span that starts within the phrase's line.
            near = any(
                m.start() - 40 <= s <= m.end() + 40 or s <= m.start() <= e
                for s, e in covered.get(rel, [])
            )
            if not (inside or near):
                line = text.count("\n", 0, m.start()) + 1
                unregistered.append(f"{rel}:{line}: {m.group(0)!r}")
    assert not unregistered, (
        "count-shaped phrases with no row in REGISTRY (add a row, or a FROZEN entry "
        "with its reason):\n" + "\n".join(unregistered)
    )


# ---- the status source itself --------------------------------------------------------


def test_the_status_source_says_v1_is_closed_when_the_readme_says_so() -> None:
    readme = _read("README.md")
    says_complete = "**v1 complete.**" in readme
    assert says_complete == all_phases_closed(), (
        "README's v1 status and the phase table in docs/technical-reference.md disagree"
    )
