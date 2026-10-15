"""The published eval results pin which cases pass, and CI replays them.

test_published_results.py pins counts and the misses. A count stays green when one
case starts passing and another stops, and a case silently dropped from a fixture
set moves the denominator with it. This pins membership both ways: the exact set
of passing case ids per suite (tests/published_case_ids.json), checked against the
suite's own fixture file so a case cannot be added or removed unnoticed, and
consistent with the misses and the published counts so the three tables cannot
drift apart.

The CI half is structural: the workflow must replay every published suite through
the CLI, so a broken `eval` command or wiring fails a pipeline step, not only a
unit test, and its actions stay pinned to commit SHAs.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

import pytest
import yaml

from tests.test_published_results import PUBLISHED, PUBLISHED_MISSES, _replay

ROOT = Path(__file__).resolve().parents[1]
PINNED = json.loads((ROOT / "tests" / "published_case_ids.json").read_text(encoding="utf-8"))

# Where each suite's cases are authored, one entry per case with an `id`.
FIXTURES = {
    "jd_extraction": "evals/jd_extraction/cases.json",
    "fit_scoring": "evals/fit_scoring/cases.json",
    "routing": "evals/routing/cases.json",
    "composition": "evals/composition/cases.json",
}


@cache
def _results(suite: str) -> dict[str, bool]:
    return {r.case_id: r.passed for r in _replay(suite).results}


def _fixture_ids(suite: str) -> set[str]:
    data = json.loads((ROOT / FIXTURES[suite]).read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    return {c.get("id") or c.get("case_id") for c in cases}


def test_every_published_suite_has_a_pinned_id_list() -> None:
    assert set(PINNED) == set(PUBLISHED)


@pytest.mark.parametrize("suite", sorted(PUBLISHED))
def test_the_cases_that_pass_are_exactly_the_pinned_ids(suite: str) -> None:
    passing = {cid for cid, ok in _results(suite).items() if ok}
    pinned = set(PINNED[suite])
    assert passing - pinned == set(), f"{suite}: newly passing, not pinned"
    assert pinned - passing == set(), f"{suite}: pinned as passing, now failing or gone"


@pytest.mark.parametrize("suite", sorted(PUBLISHED))
def test_the_pinned_ids_agree_with_the_published_counts_and_misses(suite: str) -> None:
    passed, total = PUBLISHED[suite]
    pinned = PINNED[suite]
    assert len(pinned) == len(set(pinned)), "a pinned id is listed twice"
    assert len(pinned) == passed
    assert len(pinned) + len(PUBLISHED_MISSES[suite]) == total
    assert set(pinned).isdisjoint(PUBLISHED_MISSES[suite])


@pytest.mark.parametrize("suite", sorted(FIXTURES))
def test_pinned_and_missed_ids_are_exactly_the_suites_fixture_cases(suite: str) -> None:
    accounted = set(PINNED[suite]) | PUBLISHED_MISSES[suite]
    assert accounted == _fixture_ids(suite)


# ---- CI wiring ---------------------------------------------------------------------

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


@pytest.mark.parametrize("suite", sorted(PUBLISHED))
def test_ci_replays_each_published_suite_through_the_cli(suite: str) -> None:
    commands = "\n".join(step.get("run", "") for step in _steps())
    pattern = rf"jscc eval {suite}\b[^\n]*--replay"
    assert re.search(pattern, commands), f"ci.yml never replays {suite}"


def test_the_replay_step_gets_no_api_key() -> None:
    """A replay must not be able to make a billed call."""
    for step in _steps():
        if "--replay" in step.get("run", ""):
            assert "ANTHROPIC_API_KEY" not in json.dumps(step)


def test_every_ci_action_is_pinned_to_a_commit_sha() -> None:
    uses = [step["uses"] for step in _steps() if "uses" in step]
    assert uses, "no actions found; the workflow parse is wrong"
    unpinned = [u for u in uses if not re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u)]
    assert unpinned == []


def test_ci_checks_the_feature_list() -> None:
    commands = "\n".join(step.get("run", "") for step in _steps())
    assert re.search(r"python3? scripts/features\.py check\b", commands)
