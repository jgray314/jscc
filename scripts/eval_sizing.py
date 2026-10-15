"""Pre-capture sizing gate for an eval round.

A manual capture round costs one fresh chat per case, and a round found
undersized, unbalanced or unable to discriminate afterwards is money already
spent. Run this on the fixture set you are about to capture, before capturing:

    python scripts/eval_sizing.py <suite>                  # the tracked fixtures
    python scripts/eval_sizing.py <suite> --cases PATH     # a proposed fixture file
    python scripts/eval_sizing.py <suite> --bar 0.90       # a bar other than the suite's own

Suites: jd_extraction, fit_scoring, routing, composition. Exit 1 on any finding.

Each check has a name, printed with every finding:

    undersized-round         standard error at the bar is too wide for the case count
    class-imbalance          one expected class dominates, or a class is too thin to gate on
    duplicate-case-id        two cases share an id
    duplicate-input          two cases present the same input after normalization
    near-duplicate-input     two cases share almost every word of their input
    constant-answer-baseline fit bands that one constant score mostly satisfies
    non-discriminating-case  a case no answer can fail or satisfy (inverted or
                             overwide band, nothing graded, no expectations)
    paste-ceiling            a case's input is too large to paste into a chat

The thresholds are module constants with the reason beside each. The gate reads
fixture files only; it makes no model call and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jscc import evals  # noqa: E402

# Standard error of a pass rate at the bar, sqrt(p(1-p)/n), must be at most this.
# n=25 at an 80% bar is 0.080 (the size the fit_scoring suite was resized to);
# n=15, 12 and 10, the sizes jd_extraction shipped at before it was resized, are
# 0.103, 0.115 and 0.126.
MAX_STANDARD_ERROR = 0.085

# The largest expected class may not exceed this share of the round. A constant
# answer then cannot clear a bar of 0.75 or more by class frequency alone, and a
# round that can is measuring the label prior, not the prompt.
MAX_MAJORITY_SHARE = 0.70

# Fewest cases any one class may have. A gate of "zero misses in this class" (the
# false-routine gate) is only evidence with enough cases behind it: zero misses in
# k cases leaves a 95% upper bound near 3/k.
MIN_CLASS_COUNT = 5

# Two cases whose input word sets overlap at least this much are one case counted
# twice. Fixtures written from one template differ in a name and little else.
NEAR_DUPLICATE_JACCARD = 0.85

# A score band wider than this admits most of the plausible answers, so a pass
# says little. The tracked suite's widest band is 50 points.
MAX_BAND_WIDTH = 60.0

# Assumed, not measured: the largest case input (characters) that goes into a
# chat paste as typed text. Claude.ai can turn a long paste into an attachment,
# which the model reads differently from the prompt the recording is keyed on.
# The longest tracked input is about 3,000 characters. Raise it only after
# pasting a case of that size and reading what the model was shown.
CHAT_PASTE_CEILING_CHARS = 20_000

# Id and timestamp keys carry no input; two cases differing only there are twins.
_NON_INPUT_KEYS = frozenset(
    {
        "id",
        "application_id",
        "created_at",
        "updated_at",
        "occurred_at",
        "applied_at",
        "next_action_due",
        "group",
        "expected",
        "expected_classification",
        "min_score",
        "max_score",
        "must_include",
        "must_not_include",
        "expect_needs_input",
        "forbidden",
    }
)

_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Finding:
    check: str
    case_ids: tuple[str, ...]
    message: str


def _suite_bar(suite: str) -> float:
    return {
        "jd_extraction": evals.PASS_THRESHOLD,
        "fit_scoring": evals.PASS_THRESHOLD,
        "routing": evals.ROUTING_PASS_THRESHOLD,
        "composition": evals.COMPOSITION_PASS_THRESHOLD,
    }[suite]


def min_cases_for_bar(bar: float) -> int:
    """Smallest n whose standard error at `bar` is within MAX_STANDARD_ERROR."""
    return math.ceil(bar * (1 - bar) / MAX_STANDARD_ERROR**2)


def _input_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in _NON_INPUT_KEYS:
                yield from _input_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _input_strings(item)


def _input_text(case: dict[str, Any]) -> str:
    return " ".join(_input_strings(case))


def _words(case: dict[str, Any]) -> frozenset[str]:
    return frozenset(w.lower() for w in _WORD.findall(_input_text(case)))


def _cid(case: dict[str, Any], index: int) -> str:
    return str(case.get("id") or case.get("case_id") or f"#{index}")


# --- class labels -----------------------------------------------------------------


def _routing_label(case: dict[str, Any]) -> str:
    return str(case.get("expected_classification"))


def _composition_label(case: dict[str, Any]) -> str:
    return "must-ask" if case.get("expect_needs_input") else "draft"


@dataclass(frozen=True)
class _Labeling:
    label: Callable[[dict[str, Any]], str]
    # Classes a gate is stated over, each needing MIN_CLASS_COUNT cases.
    gated: tuple[str, ...]
    # Whether a constant answer can pass by label frequency. True for routing,
    # where "always non_routine" is a valid reply; false for composition, where a
    # draft is graded on its content, so only the must-ask floor applies.
    constant_answer_passes: bool


# A suite with no entry has no class to balance: jd_extraction grades field values,
# and fit_scoring is covered by the constant-answer-baseline check instead.
_LABELS: dict[str, _Labeling] = {
    "routing": _Labeling(_routing_label, ("routine", "non_routine"), True),
    "composition": _Labeling(_composition_label, ("must-ask",), False),
}


# --- checks -----------------------------------------------------------------------


def _undersized_round(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    n = len(cases)
    need = min_cases_for_bar(bar)
    if n >= need:
        return []
    se = math.sqrt(bar * (1 - bar) / n) if n else float("inf")
    return [
        Finding(
            "undersized-round",
            (),
            f"n={n} at a {bar:.0%} bar has a standard error of {se:.3f}; "
            f"at least {need} cases are needed to bring it to {MAX_STANDARD_ERROR}.",
        )
    ]


def _class_imbalance(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    if suite not in _LABELS or not cases:
        return []
    labeling = _LABELS[suite]
    counts = Counter(labeling.label(c) for c in cases)
    n = len(cases)
    problems: list[str] = []
    top, top_n = counts.most_common(1)[0]
    if labeling.constant_answer_passes and top_n / n > MAX_MAJORITY_SHARE:
        problems.append(
            f"'{top}' is {top_n}/{n} ({top_n / n:.0%}), over the {MAX_MAJORITY_SHARE:.0%} cap, "
            "so a constant answer scores near the bar"
        )
    for cls in labeling.gated:
        if counts[cls] < MIN_CLASS_COUNT:
            problems.append(
                f"'{cls}' has {counts[cls]} cases, under the floor of {MIN_CLASS_COUNT}"
            )
    if not problems:
        return []
    return [Finding("class-imbalance", (), "; ".join(problems) + ".")]


def _duplicate_case_id(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    counts = Counter(_cid(c, i) for i, c in enumerate(cases))
    dupes = tuple(sorted(cid for cid, k in counts.items() if k > 1))
    if not dupes:
        return []
    return [
        Finding(
            "duplicate-case-id",
            dupes,
            "case ids must be unique; a recording keyed by id is overwritten by the twin.",
        )
    ]


def _normalized(case: dict[str, Any]) -> str:
    return " ".join(w.lower() for w in _WORD.findall(_input_text(case)))


def _duplicate_input(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    seen: dict[str, list[str]] = {}
    for i, case in enumerate(cases):
        seen.setdefault(_normalized(case), []).append(_cid(case, i))
    return [
        Finding(
            "duplicate-input",
            tuple(ids),
            "same input after ignoring ids, case and whitespace; one chat is spent on a repeat.",
        )
        for ids in seen.values()
        if len(ids) > 1
    ]


def _expectation(case: dict[str, Any]) -> str:
    """What the case asserts about the answer, apart from its input and identity."""
    keys = (
        "expected",
        "expected_classification",
        "min_score",
        "max_score",
        "must_include",
        "must_not_include",
        "expect_needs_input",
        "forbidden",
    )
    return json.dumps({k: case.get(k) for k in keys}, sort_keys=True, default=str)


def _near_duplicate_input(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    words = [_words(c) for c in cases]
    keys = [_normalized(c) for c in cases]
    findings: list[Finding] = []
    for i in range(len(cases)):
        for j in range(i + 1, len(cases)):
            if keys[i] == keys[j]:
                continue  # reported as duplicate-input
            if _expectation(cases[i]) != _expectation(cases[j]):
                continue  # a minimal pair: same input but one detail, opposite expectation
            union = words[i] | words[j]
            if not union:
                continue
            jac = len(words[i] & words[j]) / len(union)
            if jac >= NEAR_DUPLICATE_JACCARD:
                findings.append(
                    Finding(
                        "near-duplicate-input",
                        (_cid(cases[i], i), _cid(cases[j], j)),
                        f"inputs share {jac:.0%} of their words "
                        f"(limit {NEAR_DUPLICATE_JACCARD:.0%}); likely one case counted twice.",
                    )
                )
    return findings


def _fit_cannot_discriminate(case: dict[str, Any]) -> str | None:
    lo, hi = float(case["min_score"]), float(case["max_score"])
    if lo > hi:
        return f"band {lo:g}-{hi:g} is inverted; no score satisfies it"
    if hi - lo > MAX_BAND_WIDTH:
        return f"band {lo:g}-{hi:g} is {hi - lo:g} wide, over {MAX_BAND_WIDTH:g}"
    return None


def _extraction_cannot_discriminate(case: dict[str, Any]) -> str | None:
    expected = case.get("expected") or {}
    graded = [f for f in evals._GRADED_FIELDS if f != "must_have_skills"]
    if any(expected.get(f) is not None for f in graded) or expected.get("must_have_skills"):
        return None
    return "every graded field is null or empty, so an all-null extraction passes"


def _composition_cannot_discriminate(case: dict[str, Any]) -> str | None:
    if case.get("must_include") or case.get("must_not_include"):
        return None
    return "no must_include or must_not_include group, so only the generic length checks apply"


_DISCRIMINATION: dict[str, Callable[[dict[str, Any]], str | None]] = {
    "fit_scoring": _fit_cannot_discriminate,
    "jd_extraction": _extraction_cannot_discriminate,
    "composition": _composition_cannot_discriminate,
}


def _constant_answer_baseline(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    """The best single score a stub could return must not clear most bands."""
    if suite != "fit_scoring" or not cases:
        return []
    best_score, best_hits = 0, -1
    for score in range(101):
        hits = sum(float(c["min_score"]) <= score <= float(c["max_score"]) for c in cases)
        if hits > best_hits:
            best_score, best_hits = score, hits
    share = best_hits / len(cases)
    if share <= MAX_MAJORITY_SHARE:
        return []
    return [
        Finding(
            "constant-answer-baseline",
            (),
            f"a stub returning {best_score} passes {best_hits}/{len(cases)} ({share:.0%}), "
            f"over the {MAX_MAJORITY_SHARE:.0%} cap; the bands do not separate good from "
            "constant answers.",
        )
    ]


def _non_discriminating_case(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    test = _DISCRIMINATION.get(suite)
    if test is None:
        return []
    findings = []
    for i, case in enumerate(cases):
        reason = test(case)
        if reason:
            findings.append(Finding("non-discriminating-case", (_cid(case, i),), reason + "."))
    return findings


def _paste_ceiling(suite: str, cases: list[dict[str, Any]], bar: float) -> list[Finding]:
    return [
        Finding(
            "paste-ceiling",
            (_cid(case, i),),
            f"input is {size:,} characters, over the {CHAT_PASTE_CEILING_CHARS:,} chat paste "
            "ceiling; the chat may attach it instead of reading it as typed.",
        )
        for i, case in enumerate(cases)
        if (size := len(_input_text(case))) > CHAT_PASTE_CEILING_CHARS
    ]


_CHECKS: dict[str, Callable[[str, list[dict[str, Any]], float], list[Finding]]] = {
    "undersized-round": _undersized_round,
    "class-imbalance": _class_imbalance,
    "duplicate-case-id": _duplicate_case_id,
    "duplicate-input": _duplicate_input,
    "near-duplicate-input": _near_duplicate_input,
    "constant-answer-baseline": _constant_answer_baseline,
    "non-discriminating-case": _non_discriminating_case,
    "paste-ceiling": _paste_ceiling,
}
CHECK_NAMES = tuple(_CHECKS)

CASES_PATHS = {
    "jd_extraction": evals.JD_EXTRACTION_CASES_PATH,
    "fit_scoring": evals.FIT_SCORING_CASES_PATH,
    "routing": evals.ROUTING_CASES_PATH,
    "composition": evals.COMPOSITION_CASES_PATH,
}
SUITES = tuple(CASES_PATHS)

# Findings the tracked fixtures are known to carry, by suite. Empty means a clean
# gate; an entry needs a reason beside it and is a decision, not a default.
TRACKED_WAIVERS: dict[str, tuple[str, ...]] = {
    # 3 must-ask cases of 28. The gate "every must-ask case asks" has zero misses
    # across 3 cases, a 95% upper bound near 63%. Real, known, and not a sizing
    # defect of the captured round; widening it means new cases and a recapture
    # of only those, which is a decision for the suite's owner, not this gate.
    "composition": ("class-imbalance",),
}


def check_round(suite: str, cases: list[dict[str, Any]], bar: float | None = None) -> list[Finding]:
    if suite not in CASES_PATHS:
        raise ValueError(f"unknown suite {suite!r}; expected one of {', '.join(SUITES)}")
    use_bar = _suite_bar(suite) if bar is None else bar
    findings: list[Finding] = []
    for check in _CHECKS.values():
        findings.extend(check(suite, cases, use_bar))
    return findings


def _load(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) else data


def check_tracked_suite(suite: str) -> list[Finding]:
    return check_round(suite, _load(CASES_PATHS[suite]))


def format_findings(suite: str, findings: list[Finding]) -> str:
    if not findings:
        return f"{suite}: sizing gate clean."
    lines = [f"{suite}: {len(findings)} sizing finding(s)."]
    for f in findings:
        ids = f" [{', '.join(f.case_ids)}]" if f.case_ids else ""
        lines.append(f"  {f.check}{ids}: {f.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("suite", choices=SUITES)
    parser.add_argument("--cases", type=Path, help="a proposed fixture file (default: tracked)")
    parser.add_argument("--bar", type=float, help="pass bar to size against (default: the suite's)")
    args = parser.parse_args(argv)
    cases = _load(args.cases or CASES_PATHS[args.suite])
    findings = check_round(args.suite, cases, args.bar)
    print(format_findings(args.suite, findings))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
