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

from .paths import PACKAGE_ROOT
from .models import ExtractedJD
from .sanitizer import LLMSendError, SanitizerRefusal

JD_EXTRACTION_CASES_PATH = PACKAGE_ROOT / "evals" / "jd_extraction" / "cases.json"

# Structural fields, each with the comparison rule its content actually
# warrants. Gate finding H2: `title` and `location` were absent from every
# tuple below, so the harness read their expectations out of `cases.json`
# (all 15 cases specify both) and silently dropped them. `title` is the only
# extracted field with a production consumer -- `cli.py` uses it to name the
# Application -- so the eval suite was not grading the one field the product
# reads.
#
#   level, remote_policy  -- closed vocabularies, exact match
#   title                 -- normalized match: strict on content, forgiving
#                            on case and whitespace, which are formatting
#                            noise rather than extraction errors
#   must_have_skills      -- set equality over normalized strings, so order
#                            and casing don't matter but wording still does
#                            ("Postgres" vs "PostgreSQL" should fail; that's
#                            a real difference to pin a prompt on)
#   comp_band             -- presence only; exact dollar figures are too
#                            brittle to pin a prompt to (eval strategy doc)
#   location              -- presence, plus containment when both are present.
#                            Presence carries real signal (a remote-only role
#                            should yield null), but "Denver" vs "Denver, CO"
#                            is not an extraction failure while "Denver" vs
#                            "Seattle" is -- containment separates the two.
_EXACT_FIELDS = ("level", "remote_policy")
_NORMALIZED_FIELDS = ("title",)
_SET_FIELDS = ("must_have_skills",)
_PRESENCE_FIELDS = ("comp_band",)
_LOCATION_FIELDS = ("location",)
_PROSE_FIELDS = ("responsibilities_summary",)

_GRADED_FIELDS = (
    *_EXACT_FIELDS,
    *_NORMALIZED_FIELDS,
    *_SET_FIELDS,
    *_PRESENCE_FIELDS,
    *_LOCATION_FIELDS,
)


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


def _normalize(value: Any) -> str:
    """Casefold and collapse whitespace. Formatting noise, not content."""
    return " ".join(str(value).split()).casefold()


def _grade_field(field: str, expected: Any, actual: Any) -> FieldDiff | None:
    diff = FieldDiff(field=field, expected=expected, actual=actual)

    if field in _PRESENCE_FIELDS:
        return diff if (expected is None) != (actual is None) else None

    if field in _LOCATION_FIELDS:
        if (expected is None) != (actual is None):
            return diff
        if expected is None:
            return None
        exp, act = _normalize(expected), _normalize(actual)
        return None if (exp in act or act in exp) else diff

    if field in _SET_FIELDS:
        exp_set = {_normalize(v) for v in (expected or [])}
        act_set = {_normalize(v) for v in (actual or [])}
        return diff if exp_set != act_set else None

    if field in _NORMALIZED_FIELDS:
        return diff if _normalize(expected) != _normalize(actual) else None

    # exact fields
    return diff if expected != actual else None


def grade_extraction(case: EvalCase, extracted: ExtractedJD) -> EvalCaseResult:
    actual = extracted.model_dump()
    diffs: list[FieldDiff] = []
    for field in _GRADED_FIELDS:
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
        except (SanitizerRefusal, LLMSendError):
            # Gate finding M3: these are D7/D8 boundary failures, not prompt
            # quality signals. The generic handler below used to fold them
            # into the pass/fail count, so a sanitizer refusal across all 15
            # cases reported as a routine `0/15 passed` -- indistinguishable
            # from a bad prompt, and directly against `sanitizer.py`'s own
            # instruction that callers must not catch and continue. A safety
            # failure during an eval run has to stop the run.
            raise
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
