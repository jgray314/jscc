"""Eval harness — Slice B1 (jd_extraction), hand-rolled per the sub-plan's
"hand-rolled Python + JSON expectations for slice 1-3, revisit at slice 4."

Grading splits per the eval strategy doc: structural fields get exact (or
presence) comparison; prose fields are recorded but not machine-graded here
— an LLM-judge rubric is a later slice, once there's a real prompt whose
prose is worth judging. A case with an ungraded prose field can still fail
on its structural fields.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .models import ExtractedJD

JD_EXTRACTION_CASES_PATH = Path(__file__).resolve().parent.parent / "evals" / "jd_extraction" / "cases.json"

# Structural fields graded by comparison rule. "level", "remote_policy" are
# exact-match; "must_have_skills" is set-equality (order shouldn't matter);
# "comp_band" is presence-only (exact dollar figures are too brittle to pin
# a prompt to, per the eval strategy doc).
_EXACT_FIELDS = ("level", "remote_policy")
_SET_FIELDS = ("must_have_skills",)
_PRESENCE_FIELDS = ("comp_band",)
_PROSE_FIELDS = ("responsibilities_summary",)


class EvalCase(BaseModel):
    id: str
    raw_jd: str
    expected: dict[str, Any]


class FieldDiff(BaseModel):
    field: str
    expected: Any
    actual: Any


class EvalCaseResult(BaseModel):
    case_id: str
    passed: bool
    error: str | None = None
    diffs: list[FieldDiff] = []


class EvalSummary(BaseModel):
    total: int
    passed: int
    results: list[EvalCaseResult]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def load_cases(path: Path = JD_EXTRACTION_CASES_PATH) -> list[EvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [EvalCase(**item) for item in raw]


def _grade_field(field: str, expected: Any, actual: Any) -> FieldDiff | None:
    if field in _PRESENCE_FIELDS:
        if (expected is None) != (actual is None):
            return FieldDiff(field=field, expected=expected, actual=actual)
        return None
    if field in _SET_FIELDS:
        if set(expected or []) != set(actual or []):
            return FieldDiff(field=field, expected=expected, actual=actual)
        return None
    # exact fields
    if expected != actual:
        return FieldDiff(field=field, expected=expected, actual=actual)
    return None


def grade_extraction(case: EvalCase, extracted: ExtractedJD) -> EvalCaseResult:
    actual = extracted.model_dump()
    diffs: list[FieldDiff] = []
    for field in (*_EXACT_FIELDS, *_SET_FIELDS, *_PRESENCE_FIELDS):
        diff = _grade_field(field, case.expected.get(field), actual.get(field))
        if diff is not None:
            diffs.append(diff)
    # Prose fields are recorded, not graded — no diff, just presence check
    # that the extractor produced something non-empty.
    for field in _PROSE_FIELDS:
        if not (actual.get(field) or "").strip():
            diffs.append(FieldDiff(field=field, expected="<non-empty>", actual=actual.get(field)))
    return EvalCaseResult(case_id=case.id, passed=not diffs, diffs=diffs)


def run_jd_extraction_evals(
    extract_fn: Callable[[str], ExtractedJD],
    cases_path: Path = JD_EXTRACTION_CASES_PATH,
) -> EvalSummary:
    cases = load_cases(cases_path)
    results: list[EvalCaseResult] = []
    for case in cases:
        try:
            extracted = extract_fn(case.raw_jd)
        except Exception as e:  # extractor stub, prompt bugs, etc. — all count as a failed case
            results.append(EvalCaseResult(case_id=case.id, passed=False, error=str(e)))
            continue
        results.append(grade_extraction(case, extracted))
    passed = sum(1 for r in results if r.passed)
    return EvalSummary(total=len(results), passed=passed, results=results)


def format_eval_summary(summary: EvalSummary) -> str:
    lines = [f"{summary.passed}/{summary.total} passed ({summary.pass_rate:.0%})", ""]
    for result in summary.results:
        if result.passed:
            lines.append(f"  [PASS] {result.case_id}")
            continue
        lines.append(f"  [FAIL] {result.case_id}")
        if result.error:
            lines.append(f"    error: {result.error}")
        for diff in result.diffs:
            lines.append(f"    {diff.field}: expected={diff.expected!r} actual={diff.actual!r}")
    return "\n".join(lines)
