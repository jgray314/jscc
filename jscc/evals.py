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
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import Profile
from .llm_client import STUB_CLIENTS, LLMResponse
from .models import Application, DraftEmail, ExtractedJD, FitResult, Interaction, RoutingDecision
from .paths import PACKAGE_ROOT
from .sanitizer import LLMSendError, SanitizerRefusal

JD_EXTRACTION_CASES_PATH = PACKAGE_ROOT / "evals" / "jd_extraction" / "cases.json"
JD_EXTRACTION_RECORDING_PATH = PACKAGE_ROOT / "evals" / "jd_extraction" / "recorded.json"
FIT_SCORING_CASES_PATH = PACKAGE_ROOT / "evals" / "fit_scoring" / "cases.json"
FIT_SCORING_RECORDING_PATH = PACKAGE_ROOT / "evals" / "fit_scoring" / "recorded.json"
ROUTING_CASES_PATH = PACKAGE_ROOT / "evals" / "routing" / "cases.json"
ROUTING_RECORDING_PATH = PACKAGE_ROOT / "evals" / "routing" / "recorded.json"
COMPOSITION_CASES_PATH = PACKAGE_ROOT / "evals" / "composition" / "cases.json"
COMPOSITION_RECORDING_PATH = PACKAGE_ROOT / "evals" / "composition" / "recorded.json"

# Per the sub-plan's D2: routing is held to a higher combined bar (85%, not
# the 80% PASS_THRESHOLD jd_extraction/fit_scoring use) *and* a separate,
# stricter 100% bar on false-routine cases specifically -- see
# `false_routine_cases` below. Two different numbers for two different
# risks: overall accuracy vs. the one failure mode (auto-drafting something
# that needed a human) D10 calls out as categorically worse than the rest.
ROUTING_PASS_THRESHOLD = 0.85

# Per the sub-plan's D4: composition is held to 75%, lower than the other suites
# because a draft is judged on tone and phrasing, which is fuzzier than a
# classification or a field match. It has no second gate: D10 already made the
# expensive mistake (auto-drafting a non-routine situation) the router's job.
COMPOSITION_PASS_THRESHOLD = 0.75

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
    # Reported, never gating: a case can pass with advisories.
    advisories: list[FieldDiff] = Field(default_factory=list)


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
        for slot in expected or []:
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


def save_recording(responses: dict[str, str], path: Path = JD_EXTRACTION_RECORDING_PATH) -> None:
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
    "bc")` from colliding, which plain concatenation would not.

    Gate finding L-14: the `sha256:` prefix isn't cosmetic. A bare hex digest
    is indistinguishable from a phone number to the pre-commit scanner's
    digit-run heuristic, which is why `recorded.json` used to be excluded
    from scanning wholesale -- covering its *values* (real model output)
    along with the keys the exclusion was actually for. `scripts/
    precommit_scan.py` strips exactly this `"sha256:<hex>"` shape before
    matching, so the file no longer needs the blanket exclude and its values
    get scanned like everything else."""
    digest = hashlib.sha256(f"{model}\0{system}\0{user}".encode()).hexdigest()
    return f"sha256:{digest}"


class ManualCaptureClient:
    """An `LLMClient` whose "network call" is a human pasting a prompt into
    Claude.ai chat and pasting the completion back -- the mechanism C2b (and
    B2b before it) needs when no `ANTHROPIC_API_KEY` is configured for this
    project. Wrap it in `RecordingClient` to get M-12's persist-immediately
    behavior for free; nothing about capture-safety needed reinventing here.

    `input_fn`/`output_fn` are injectable so tests can drive this without a
    real terminal. Real usage takes the defaults: `output_fn` prints the
    exact system+user text the eval harness would have sent a real client,
    and `input_fn` reads the pasted-back response line by line until a line
    that is exactly `END` (a completion can itself contain blank lines, so a
    single blank line can't be the sentinel).

    Reports zero tokens/cost, honestly -- no billed API call happened."""

    _END_SENTINEL = "END"

    def __init__(
        self,
        *,
        input_fn: Callable[[], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self._input_fn = input_fn
        self._output_fn = output_fn

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        out = self._output_fn
        out("=" * 70)
        out(f"MODEL: {model}")
        out("--- SYSTEM PROMPT (paste into Claude.ai chat as the system prompt) ---")
        out(system)
        out("--- USER MESSAGE ---")
        out(user)
        out("=" * 70)
        out(
            f"Paste the model's full response below, then a line containing only {self._END_SENTINEL}:"
        )
        lines: list[str] = []
        while True:
            line = self._input_fn()
            if line.strip() == self._END_SENTINEL:
                break
            lines.append(line)
        return LLMResponse(
            text="\n".join(lines),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            stop_reason="end_turn",
        )


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
        if isinstance(inner, STUB_CLIENTS):
            # Recording merges into `recorded.json` by prompt key, so recording the
            # stub would replace every hand-captured response with placeholder text.
            raise ValueError(
                f"refusing to record {type(inner).__name__}: its output is a placeholder, "
                "not model output"
            )
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

    def __init__(self, recorded: dict[str, str], *, suite: str = "<suite>") -> None:
        self._recorded = recorded
        self._suite = suite

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        key = _prompt_key(model, system, user)
        try:
            text = self._recorded[key]
        except KeyError:
            raise RecordingMissing(
                "no recorded response for this prompt. The prompt or the redaction "
                "rules changed since the recording was made; re-record with "
                f"`eval {self._suite} --record` against a live key or with --manual, "
                "rather than editing the recording by hand."
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
            for a in result.advisories:
                lines.append(f"    advisory {a.field}: {a.actual}")
            continue
        lines.append(f"  [FAIL] {result.case_id}")
        if result.error:
            lines.append(f"    error: {result.error}")
        for diff in result.diffs:
            lines.append(f"    {diff.field}: expected={diff.expected!r} actual={diff.actual!r}")
    return "\n".join(lines)


# --- fit_scoring (Slice C1) ---------------------------------------------
#
# Per the eval strategy doc, band placement is graded, not an exact score:
# "expected score bands (not exact scores) — bands like 'high fit 75-95',
# 'clear pass <30'". `rationale` is checked for non-empty only, the same
# treatment `responsibilities_summary` gets above — real quality grading
# (does the rationale actually name the right factors?) is an LLM-judge
# rubric, deferred until there's a real prompt worth judging.


class FitEvalCase(BaseModel):
    id: str
    extracted_jd: dict[str, Any]
    raw_jd_text: str
    profile: dict[str, Any]
    min_score: float
    max_score: float


def load_fit_cases(path: Path = FIT_SCORING_CASES_PATH) -> list[FitEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [FitEvalCase(**item) for item in raw]


def grade_fit_score(case: FitEvalCase, result: FitResult) -> EvalCaseResult:
    diffs: list[FieldDiff] = []
    if not (case.min_score <= result.score <= case.max_score):
        diffs.append(
            FieldDiff(
                field="score",
                expected=f"[{case.min_score}, {case.max_score}]",
                actual=result.score,
            )
        )
    if not result.rationale.strip():
        diffs.append(FieldDiff(field="rationale", expected="<non-empty>", actual=result.rationale))
    return EvalCaseResult(case_id=case.id, passed=not diffs, diffs=diffs)


def run_fit_scoring_evals(
    score_fn: Callable[[ExtractedJD, str, Profile], FitResult],
    cases_path: Path = FIT_SCORING_CASES_PATH,
) -> EvalSummary:
    cases = load_fit_cases(cases_path)
    results: list[EvalCaseResult] = []
    for case in cases:
        try:
            extracted = ExtractedJD(**case.extracted_jd)
            profile = Profile(**case.profile)
            result = score_fn(extracted, case.raw_jd_text, profile)
        except (SanitizerRefusal, LLMSendError):
            raise
        except Exception as e:  # scorer stub, prompt bugs, malformed fixtures, etc.
            results.append(EvalCaseResult(case_id=case.id, passed=False, error=str(e)))
            continue
        results.append(grade_fit_score(case, result))
    passed = sum(1 for r in results if r.passed)
    return EvalSummary(total=len(results), passed=passed, results=results)


# --- routing (Slice D1) --------------------------------------------------
#
# Per D10, the router's job is a binary classification (routine /
# non_routine), not a draft -- so grading checks the classification first,
# since a wrong classification is the case-defining failure, then checks
# presence of the shape-appropriate fields (`intent` for routine, `reason`
# + `considerations` for non_routine) the same way `fit_scoring`'s
# `rationale` got a presence-only check at C1: real wording-quality grading
# (is `intent` the RIGHT routine bucket, are `considerations` actually
# useful) is deferred to Slice D2, once there's a real prompt worth judging.


class RoutingEvalCase(BaseModel):
    id: str
    application: dict[str, Any]
    history: list[dict[str, Any]]
    expected_classification: str  # "routine" | "non_routine"


def load_routing_cases(path: Path = ROUTING_CASES_PATH) -> list[RoutingEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [RoutingEvalCase(**item) for item in raw]


def grade_routing_decision(case: RoutingEvalCase, decision: RoutingDecision) -> EvalCaseResult:
    diffs: list[FieldDiff] = []
    actual_classification = decision.classification.value
    if actual_classification != case.expected_classification:
        diffs.append(
            FieldDiff(
                field="classification",
                expected=case.expected_classification,
                actual=actual_classification,
            )
        )
    elif case.expected_classification == "routine":
        # `RoutingDecision` already rejects these shapes at parse time; the grader
        # checks again so a decision built without validation cannot pass either.
        if not (decision.intent or "").strip():
            diffs.append(FieldDiff(field="intent", expected="<non-empty>", actual=decision.intent))
        if (decision.reason or "").strip() or decision.considerations:
            diffs.append(
                FieldDiff(
                    field="considerations",
                    expected="<none on a routine decision>",
                    actual=[decision.reason, *decision.considerations],
                )
            )
    else:
        if not (decision.reason or "").strip():
            diffs.append(FieldDiff(field="reason", expected="<non-empty>", actual=decision.reason))
        if not decision.considerations:
            diffs.append(
                FieldDiff(
                    field="considerations", expected="<non-empty>", actual=decision.considerations
                )
            )
    return EvalCaseResult(case_id=case.id, passed=not diffs, diffs=diffs)


def run_routing_evals(
    route_fn: Callable[[Application, list[Interaction]], RoutingDecision],
    cases_path: Path = ROUTING_CASES_PATH,
) -> EvalSummary:
    cases = load_routing_cases(cases_path)
    results: list[EvalCaseResult] = []
    for case in cases:
        try:
            app = Application(**case.application)
            history = [Interaction(**item) for item in case.history]
            decision = route_fn(app, history)
        except (SanitizerRefusal, LLMSendError):
            raise
        except Exception as e:  # router stub, prompt bugs, malformed fixtures, etc.
            results.append(EvalCaseResult(case_id=case.id, passed=False, error=str(e)))
            continue
        results.append(grade_routing_decision(case, decision))
    passed = sum(1 for r in results if r.passed)
    return EvalSummary(total=len(results), passed=passed, results=results)


def false_routine_cases(summary: EvalSummary) -> list[str]:
    """Case ids where a genuinely `non_routine` situation was classified
    `routine` -- the failure mode D10 calls out as categorically worse than
    the rest, since it means auto-drafting something that needed a human.

    Distinguished from an ordinary non_routine miss (e.g. a missing
    `reason`) by inspecting the classification diff specifically: a case can
    fail `grade_routing_decision` for a reason that has nothing to do with
    false-routine (right bucket, missing `considerations`), and that miss
    should count against the combined pass rate without tripping the
    separate, stricter false-routine gate.
    """
    return [
        r.case_id
        for r in summary.results
        for d in r.diffs
        if d.field == "classification" and d.expected == "non_routine" and d.actual == "routine"
    ]


def routing_gate(summary: EvalSummary, min_pass_rate: float) -> list[str]:
    """Why this routing run fails its gate, or an empty list if it passes.

    Two independent conditions. The pass rate must clear `min_pass_rate`, and no
    case may be a false-routine, whatever the pass rate: auto-drafting a situation
    that needed a person is the failure the router exists to prevent. The CLI exits
    non-zero exactly when this returns anything.
    """
    failures = []
    if summary.pass_rate < min_pass_rate:
        failures.append(f"pass rate {summary.pass_rate:.0%} is below the {min_pass_rate:.0%} bar")
    false_routine = false_routine_cases(summary)
    if false_routine:
        failures.append(
            f"{len(false_routine)} false-routine case(s): a non_routine situation was "
            f"classified routine, an automatic fail regardless of the pass rate: "
            f"{', '.join(false_routine)}"
        )
    return failures


# --- composition (Slice D3) -----------------------------------------------
#
# Per D10 step 2A, composition is only ever reached for a `routine`
# situation -- there is no `expected_classification` here the way
# `RoutingEvalCase` needed one, since every fixture is routine by
# construction. Grading is presence-only for now (non-empty `subject`/
# `body`), the same deferral `responsibilities_summary` (B1), `rationale`
# (C1), and `intent`/`reason`/`considerations` (D1) all got: real quality
# grading (tone match, reference to the prior touchpoint, no hallucinated
# facts, appropriate to the declared intent) is an LLM-judge rubric, deferred
# to Slice D4 once there's a real prompt whose output is worth judging.


class CompositionEvalCase(BaseModel):
    id: str
    application: dict[str, Any]
    history: list[dict[str, Any]]
    intent: str
    style_samples: list[str] = Field(default_factory=list)
    # Per-case expectations for the deterministic grader. Each `must_include`
    # group is a list of alternatives (synonyms) and the draft needs one hit
    # from every group; `must_not_include` is the no-hallucination trap list.
    must_include: list[list[str]] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    # D4c: the case is one the router should never send here but the composer
    # must still refuse to guess on. Passing means it returned `needs_input`
    # naming the missing detail (`must_include` is checked against that text),
    # not that it drafted.
    expect_needs_input: bool = False


def load_composition_cases(path: Path = COMPOSITION_CASES_PATH) -> list[CompositionEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [CompositionEvalCase(**item) for item in raw]


# Tone and overall quality are deliberately not graded: these are the checks a
# machine can make reproducibly, so a pass means "no mechanical defect", not "a
# good email". The prompt asks for 50-130 words and a subject of 8 or fewer; the
# bounds here are looser so the grader flags real drift, not rounding.
COMPOSITION_BODY_WORDS = (30, 160)
COMPOSITION_SUBJECT_MAX_WORDS = 10
_STYLE_REUSE_MIN_WORDS = 6

_PLACEHOLDER = re.compile(r"\[[^\]]*\]|\{\{.*?\}\}|<[^>\n]+>|redacted", re.IGNORECASE)
_DIGIT_RUN = re.compile(r"\d+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*")
_CALENDAR_AND_CLOSING_WORDS = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday "
    "january february march april may june july august september october november december "
    "best thanks regards sincerely cheers".split()
)


# Short, polite phrases that are ordinary social convention. In the reuse check
# each occurrence counts as ONE unit, so echoing "wanted to check in" does not
# eat into the 6-unit budget while a whole copied sentence still does. Matching
# is on normalized text (lowercase, punctuation stripped), and longer phrases
# win over the shorter phrases they contain. Chosen from proxy runs c1-c3.
_STOCK_PHRASES_RAW = (
    "let me know if there is anything",
    "let me know if there's anything",
    "let me know if you need anything",
    "timing for next steps",
    "next steps",
    "no rush",
    "no pressure",
    "putting my name forward",
    "wanted to check in",
    "checking in",
    "just checking in",
    "thanks again for",
    "thank you for taking the time",
    "looking forward to",
    "still very excited",
    "still very interested",
    "happy to work around",
    "thanks for the flexibility",
    "really appreciate",
    "i appreciate",
    "where things stand",
)
# A draft leaning on more distinct stock phrases than this reads like a form
# letter. Advisory only, promoted to a failing check if real drafts start to.
COMPOSITION_STOCK_PHRASE_ADVISORY_ABOVE = 4


def _stock_phrases() -> list[str]:
    return sorted({_normalize_prose(p) for p in _STOCK_PHRASES_RAW}, key=len, reverse=True)


def _collapse_stock(normalized: str) -> tuple[list[str], set[str]]:
    """Tokens of `normalized` with each stock phrase replaced by one unit, plus
    the distinct phrases found. Longest phrases first, so a phrase contained in a
    longer one is not counted twice."""
    text = f" {normalized} "
    found: set[str] = set()
    for phrase in _stock_phrases():
        needle = f" {phrase} "
        if needle in text:
            found.add(phrase)
            text = text.replace(needle, " \u00a7 ")
    return text.split(), found


def _facts_corpus(case: CompositionEvalCase) -> str:
    """Text a draft may legitimately draw facts from: the application's own
    fields and the history. Style samples are excluded on purpose (they are
    voice, not facts), and so are internal ids (their digits are not facts)."""
    parts = [str(v) for k, v in case.application.items() if k != "id" and v is not None]
    for item in case.history:
        for key in ("notes", "next_action", "next_action_due", "occurred_at"):
            if item.get(key):
                parts.append(str(item[key]))
    return "\n".join(parts)


def _digit_runs(text: str) -> set[str]:
    return {str(int(m)) for m in _DIGIT_RUN.findall(text)}


def _normalize_prose(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _sentences(text: str) -> list[str]:
    return [p for p in re.split(r"[.!?\n]+", text) if p.strip()]


def grade_composition(case: CompositionEvalCase, draft: DraftEmail) -> EvalCaseResult:
    """Deterministic checks; a case passes only if every one does, and each
    failure names its check in `FieldDiff.field`."""
    diffs: list[FieldDiff] = []
    subject, body = draft.subject, draft.body

    asked = (draft.needs_input or "").strip()
    if case.expect_needs_input:
        if not asked:
            diffs.append(
                FieldDiff(
                    field="needs_input",
                    expected="a question naming the missing detail",
                    actual="a draft" if (subject.strip() or body.strip()) else "nothing",
                )
            )
        else:
            missing = [
                group
                for group in case.must_include
                if not any(alt.lower() in asked.lower() for alt in group)
            ]
            if missing:
                diffs.append(FieldDiff(field="must_include", expected=missing, actual=asked))
        return EvalCaseResult(case_id=case.id, passed=not diffs, diffs=diffs)
    if asked:
        diffs.append(FieldDiff(field="unexpected_needs_input", expected="a draft", actual=asked))
        return EvalCaseResult(case_id=case.id, passed=False, diffs=diffs)

    if not subject.strip():
        diffs.append(FieldDiff(field="subject", expected="<non-empty>", actual=subject))
    if not body.strip():
        diffs.append(FieldDiff(field="body", expected="<non-empty>", actual=body))

    for label, text in (("subject", subject), ("body", body)):
        found = _PLACEHOLDER.findall(text)
        if found:
            diffs.append(
                FieldDiff(field="placeholder", expected=f"none in {label}", actual=found[0])
            )

    lo, hi = COMPOSITION_BODY_WORDS
    body_words = len(body.split())
    if body.strip() and not lo <= body_words <= hi:
        diffs.append(FieldDiff(field="body_length", expected=f"{lo}-{hi} words", actual=body_words))
    subject_words = len(subject.split())
    if subject_words > COMPOSITION_SUBJECT_MAX_WORDS:
        diffs.append(
            FieldDiff(
                field="subject_length",
                expected=f"<= {COMPOSITION_SUBJECT_MAX_WORDS} words",
                actual=subject_words,
            )
        )

    facts = _facts_corpus(case)
    invented = sorted(_digit_runs(subject + " " + body) - _digit_runs(facts), key=int)
    if invented:
        diffs.append(
            FieldDiff(
                field="invented_number", expected="only numbers in the facts", actual=invented
            )
        )

    normalized_body = _normalize_prose(body)
    for sample in case.style_samples:
        for sentence in _sentences(sample):
            if (
                len(_collapse_stock(_normalize_prose(sentence))[0]) >= _STYLE_REUSE_MIN_WORDS
                and _normalize_prose(sentence) in normalized_body
            ):
                diffs.append(
                    FieldDiff(
                        field="style_reuse",
                        expected="no copied sample sentence",
                        actual=sentence.strip(),
                    )
                )

    known_words = {w.lower() for w in _WORD.findall(facts)} | _CALENDAR_AND_CLOSING_WORDS
    invented_names: list[str] = []
    for sentence in _sentences(body):
        for word in _WORD.findall(sentence)[1:]:
            if re.fullmatch(r"[A-Z][a-z]+", word) and word.lower() not in known_words:
                invented_names.append(word)
    if invented_names:
        diffs.append(
            FieldDiff(
                field="invented_name",
                expected="only names in the facts",
                actual=sorted(set(invented_names)),
            )
        )

    haystack = (subject + "\n" + body).lower()
    missing = [
        group for group in case.must_include if not any(alt.lower() in haystack for alt in group)
    ]
    if missing:
        diffs.append(FieldDiff(field="must_include", expected=missing, actual="no match"))
    forbidden = [term for term in case.must_not_include if term.lower() in haystack]
    if forbidden:
        diffs.append(FieldDiff(field="must_not_include", expected="absent", actual=forbidden))

    advisories: list[FieldDiff] = []
    _, stock_found = _collapse_stock(normalized_body)
    if len(stock_found) > COMPOSITION_STOCK_PHRASE_ADVISORY_ABOVE:
        advisories.append(
            FieldDiff(
                field="form_letter",
                expected=f"<= {COMPOSITION_STOCK_PHRASE_ADVISORY_ABOVE} stock phrases",
                actual=sorted(stock_found),
            )
        )

    return EvalCaseResult(case_id=case.id, passed=not diffs, diffs=diffs, advisories=advisories)


def run_composition_evals(
    compose_fn: Callable[[Application, list[Interaction], str, list[str]], DraftEmail],
    cases_path: Path = COMPOSITION_CASES_PATH,
) -> EvalSummary:
    cases = load_composition_cases(cases_path)
    results: list[EvalCaseResult] = []
    for case in cases:
        try:
            app = Application(**case.application)
            history = [Interaction(**item) for item in case.history]
            draft = compose_fn(app, history, case.intent, case.style_samples)
        except (SanitizerRefusal, LLMSendError):
            raise
        except Exception as e:  # composer stub, prompt bugs, malformed fixtures, etc.
            results.append(EvalCaseResult(case_id=case.id, passed=False, error=str(e)))
            continue
        results.append(grade_composition(case, draft))
    passed = sum(1 for r in results if r.passed)
    return EvalSummary(total=len(results), passed=passed, results=results)


def drafted_instead_of_asking(summary: EvalSummary) -> list[str]:
    """Case ids where the composer wrote a draft for a case that required it to ask.

    Those cases withhold a detail only the candidate knows, so a draft there was
    built on an invented fact. Naming the wrong detail, or failing to parse, is an
    ordinary miss; returning a draft is not.
    """
    return [
        r.case_id
        for r in summary.results
        for d in r.diffs
        if d.field == "needs_input" and d.actual == "a draft"
    ]


def composition_gate(summary: EvalSummary, min_pass_rate: float) -> list[str]:
    """Why this composition run fails its gate, or an empty list if it passes.

    Same shape as `routing_gate`: the pass rate must clear the bar, and no case that
    required the composer to ask may come back as a draft, whatever the pass rate.
    """
    failures = []
    if summary.pass_rate < min_pass_rate:
        failures.append(f"pass rate {summary.pass_rate:.0%} is below the {min_pass_rate:.0%} bar")
    invented = drafted_instead_of_asking(summary)
    if invented:
        failures.append(
            f"{len(invented)} case(s) drafted where the composer had to ask for a missing "
            f"detail, an automatic fail regardless of the pass rate: {', '.join(invented)}"
        )
    return failures
