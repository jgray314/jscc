"""Eval harness — Slice B1 (jd_extraction), hand-rolled per the sub-plan's
"hand-rolled Python + JSON expectations for slice 1-3, revisit at slice 4."

Grading splits per the eval strategy doc: structural fields get exact (or
presence) comparison; prose fields are recorded but not machine-graded here
— an LLM-judge rubric is a later slice, once there's a real prompt whose
prose is worth judging. A case with an ungraded prose field can still fail
on its structural fields.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .llm_client import LLMResponse
from .models import ExtractedJD
from .paths import PACKAGE_ROOT
from .sanitizer import LLMSendError, SanitizerRefusal

JD_EXTRACTION_CASES_PATH = PACKAGE_ROOT / "evals" / "jd_extraction" / "cases.json"
JD_EXTRACTION_RECORDING_PATH = PACKAGE_ROOT / "evals" / "jd_extraction" / "recorded.json"

# The bar the suite is held to. It lives here rather than in prose so it is a
# property of the object: a threshold in a README is a promise about a
# document, and the command has to enforce the same rule the docs claim.
PASS_THRESHOLD = 0.80

# Structural fields, each with the comparison rule its content actually
# warrants. Every field a case can specify must appear in one of these tuples:
# a field missing from all of them is read out of `cases.json` and silently
# dropped, grading the prompt against fewer expectations than the case author
# wrote. `_GRADED_FIELDS` and its coverage test make that impossible rather
# than merely unlikely.
#
#   level, remote_policy  -- closed vocabularies, exact match
#   title, company        -- normalized match: strict on content, forgiving
#                            on case and whitespace, which are formatting
#                            noise rather than extraction errors. `company`
#                            is nullable (some postings never name the
#                            employer) -- both-None normalizes to the same
#                            string and passes, one-None-one-not fails, same
#                            as any other normalized-field mismatch.
#   must_have_skills      -- word-set containment, so "spreadsheets" and
#                            "spreadsheet fluency" are the same skill in
#                            different words but "Postgres" and "PostgreSQL"
#                            are still a real difference to pin a prompt on
#                            (see `_skill_matches`). An expected entry may
#                            itself be a list of alternatives -- e.g.
#                            `["applied statistics", "data science"]` -- when
#                            the JD poses a genuine "X or Y" requirement;
#                            naming either satisfies that slot. Extras the
#                            model adds beyond every expected slot still fail
#                            the case: containment forgives wording, not
#                            scope.
#   comp_band             -- presence only; exact dollar figures are too
#                            brittle to pin a prompt to (eval strategy doc)
#   location              -- presence, plus containment when both are present.
#                            Presence carries real signal (a remote-only role
#                            should yield null), but "Denver" vs "Denver, CO"
#                            is not an extraction failure while "Denver" vs
#                            "Seattle" is -- containment separates the two.
_EXACT_FIELDS = ("level", "remote_policy")
_NORMALIZED_FIELDS = ("title", "company")
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
    # "short" (hand-authored, paste-shaped) vs "long" (synthetic-but-realistic
    # fetched-page length and noise). Reporting, not a separate gate -- see
    # `format_eval_summary`. Defaulted so the original 15 cases need no edit.
    group: str = "short"


class FieldDiff(BaseModel):
    field: str
    expected: Any
    actual: Any


class EvalCaseResult(BaseModel):
    case_id: str
    group: str = "short"
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
    """Casefold and collapse whitespace/hyphens/slashes. Formatting noise, not
    content -- "infrastructure-as-code" and "infrastructure as code" are the
    same skill, not a real difference to pin a prompt on the way "Postgres"
    vs "PostgreSQL" is. Slashes get the same treatment as hyphens for the
    same reason: "firmware/BMC" and "firmware, BMC" are two skills joined by
    punctuation, not one token -- leaving the slash in place would prevent
    either half from ever matching its own expected entry."""
    text = str(value).replace("-", " ").replace("/", " ")
    return " ".join(text.split()).casefold()


def _singularize(word: str) -> str:
    """Strip a trailing plural "s". Guarded by length so short words that
    end in "s" by coincidence ("iOS" -> "ios") aren't mangled into something
    that no longer means what it meant."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _word_set(value: Any) -> frozenset[str]:
    return frozenset(_singularize(w) for w in _normalize(value).split())


def _skill_matches(expected: str, actual: str) -> bool:
    """True if one phrase's core words are a subset of the other's --
    "spreadsheets" vs. "spreadsheet fluency", "GPU hardware" vs. "GPUs",
    "model deployment" vs. "production model deployment" all match this way.
    Word-set, not substring: raw substring would wrongly match "Go" inside
    "Google". Two unrelated skills that happen to share one common word
    ("data" alone vs. "data science") would also match here -- accepted as
    the same tradeoff `location`'s containment rule already makes, and rare
    in practice since single common-word skill entries aren't how JDs read."""
    exp, act = _word_set(expected), _word_set(actual)
    return bool(exp) and bool(act) and (exp <= act or act <= exp)


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
        remaining_actual = list(actual or [])
        unmatched_expected = []
        for slot in (expected or []):
            alternatives = slot if isinstance(slot, list) else [slot]
            matched_indices = [
                i
                for i, act_item in enumerate(remaining_actual)
                if any(_skill_matches(alt, act_item) for alt in alternatives)
            ]
            if not matched_indices:
                unmatched_expected.append(slot)
            else:
                # Consume every match, not just the first: a disjunctive
                # slot's alternatives are still one requirement, so naming
                # BOTH acceptable options (case-33: "infrastructure
                # engineering" AND "platform engineering") satisfies the slot
                # completely rather than leaving the second named option to
                # be flagged as an unexplained extra.
                for i in reversed(matched_indices):
                    remaining_actual.pop(i)
        # remaining_actual: entries the model added beyond every expected
        # slot. Containment already forgave wording -- anything left here is
        # a real scope miss (title-redundant restatement, stack-description
        # leakage, etc.), so it still fails the case.
        return diff if (unmatched_expected or remaining_actual) else None

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
    return EvalCaseResult(case_id=case.id, group=case.group, passed=not diffs, diffs=diffs)


class RecordingMissing(RuntimeError):
    """Replay was requested but no recorded response exists for a case."""


def load_recording(path: Path = JD_EXTRACTION_RECORDING_PATH) -> dict[str, str]:
    """Prompt hash -> the raw model response captured on a live run.

    Replay exists so CI can gate on the eval suite without an API key, spend,
    or the flakiness of scoring a nondeterministic model on every push. Be
    precise about what that buys: replay pins the harness, the parser, and the
    prompt's output *contract*. It does not measure the model's judgment --
    only a live run does that, and its result is what gets published. Calling
    replay "eval-gated" without that distinction would be the same kind of
    overclaim D8 is careful to avoid.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_recording(
    responses: dict[str, str], path: Path = JD_EXTRACTION_RECORDING_PATH
) -> None:
    """Merge `responses` into whatever's already on disk at `path` and write
    the result.

    Gate finding M-12: this used to overwrite the file unconditionally, so a
    `--record` over a subset of cases (a resumed run after a crash, or a
    deliberate partial re-record) silently dropped every recording that
    wasn't in this call's `responses`. Merging means the file can only gain
    or update keys that were actually captured this call -- it can't lose
    ones that weren't."""
    existing = load_recording(path)
    merged = {**existing, **responses}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prompt_key(model: str, system: str, user: str) -> str:
    """Recordings are keyed by a hash of everything that determines the
    response the client actually receives: the model id, the system prompt,
    and the (post-sanitizer) user prompt.

    Gate finding H-5: this used to hash `user` alone. The docstring already
    claimed the key covered "the prompt the client actually receives", and a
    prompt is model + system + user, not one third of it -- replacing the
    entire system prompt with unrelated text replayed the exact same
    recordings at the exact same pass rate, because nothing about the system
    prompt was in the key. A NUL separator keeps `("ab", "c")` and `("a",
    "bc")` from colliding, which plain concatenation would not."""
    return hashlib.sha256(f"{model}\0{system}\0{user}".encode("utf-8")).hexdigest()


class RecordingClient:
    """Wraps a real client and captures each response for later replay.

    Gate finding M-12: `eval --record` used to hold every capture in memory
    (`.captured`) and write it to disk only after the whole run returned --
    so the deliberate `SanitizerRefusal`/`LLMSendError` re-raise, a
    transient API error (H-6), or a Ctrl-C at case 31 of 33 discarded every
    capture from a run that had already spent the money on all of them.
    `on_captured`, when given, is called with each (key, response text) pair
    the moment it's captured, so a caller can persist it immediately rather
    than trust the run to finish."""

    def __init__(
        self, inner: Any, *, on_captured: Callable[[str, str], None] | None = None
    ) -> None:
        self._inner = inner
        self._on_captured = on_captured
        self.captured: dict[str, str] = {}

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        response = self._inner.complete(model=model, system=system, user=user)
        key = _prompt_key(model, system, user)
        self.captured[key] = response.text
        if self._on_captured is not None:
            self._on_captured(key, response.text)
        return response


class ReplayClient:
    """Serves recorded responses. Opens no socket and spends nothing, so the
    zero usage figures it reports are accurate rather than a placeholder."""

    def __init__(self, recorded: dict[str, str]) -> None:
        self._recorded = recorded

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        key = _prompt_key(model, system, user)
        try:
            text = self._recorded[key]
        except KeyError:
            raise RecordingMissing(
                "no recorded response for this prompt. The prompt or the "
                "redaction rules changed since the recording was made -- "
                "re-record with `eval jd_extraction --record` against a live "
                "key rather than editing the recording by hand."
            ) from None
        return LLMResponse(
            text=text, input_tokens=0, output_tokens=0, cost_usd=0.0, stop_reason="end_turn"
        )


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
            # D7/D8 boundary failures, not prompt-quality signals. Folded
            # into the pass/fail count they are indistinguishable from a bad
            # prompt, and `sanitizer.py` instructs callers not to catch and
            # continue. A safety failure during an eval run stops the run.
            raise
        except Exception as e:  # extractor stub, prompt bugs, etc. — all count as a failed case
            results.append(
                EvalCaseResult(case_id=case.id, group=case.group, passed=False, error=str(e))
            )
            continue
        results.append(grade_extraction(case, extracted))
    passed = sum(1 for r in results if r.passed)
    return EvalSummary(total=len(results), passed=passed, results=results)


def format_eval_summary(summary: EvalSummary) -> str:
    lines = [f"{summary.passed}/{summary.total} passed ({summary.pass_rate:.0%})"]
    # Subgroup breakdown is reporting only -- `min_pass_rate` in the CLI gates
    # on the combined figure above, per the locked B7 exit contract. This just
    # answers "which distribution broke" when the combined number drops,
    # rather than requiring a second gate to find out (D9's philosophy
    # applied to the eval fixtures, not just the extract/score split).
    groups: dict[str, list[EvalCaseResult]] = {}
    for result in summary.results:
        groups.setdefault(result.group, []).append(result)
    if len(groups) > 1:
        for group in sorted(groups):
            group_results = groups[group]
            group_passed = sum(1 for r in group_results if r.passed)
            lines.append(
                f"  {group}: {group_passed}/{len(group_results)} passed "
                f"({group_passed / len(group_results):.0%})"
            )
    lines.append("")
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
