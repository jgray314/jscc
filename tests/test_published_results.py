"""The eval results the docs publish are what the committed recordings replay to.

A grader change can move a replayed result without anyone re-reading the docs; the
composition grader's calibration moved proxy results by two or three cases. This
test replays each suite's `recorded.json` and fails if the count differs from the
table below, which is the table `evals/README.md` publishes.
"""

from __future__ import annotations

import pytest

from jscc.composition import compose_followup
from jscc.evals import (
    COMPOSITION_RECORDING_PATH,
    FIT_SCORING_RECORDING_PATH,
    JD_EXTRACTION_RECORDING_PATH,
    ROUTING_RECORDING_PATH,
    ReplayClient,
    load_recording,
    run_composition_evals,
    run_fit_scoring_evals,
    run_jd_extraction_evals,
    run_routing_evals,
)
from jscc.extraction import extract_jd
from jscc.routing import route_followup
from jscc.scoring import score_fit

# suite -> (passed, total). Change only together with evals/README.md.
PUBLISHED = {
    "jd_extraction": (32, 36),
    "fit_scoring": (27, 28),
    "routing": (37, 38),
    "composition": (24, 28),
}


def _replay(suite: str):
    if suite == "jd_extraction":
        client = ReplayClient(load_recording(JD_EXTRACTION_RECORDING_PATH))
        return run_jd_extraction_evals(lambda raw: extract_jd(raw, client=client))
    if suite == "fit_scoring":
        client = ReplayClient(load_recording(FIT_SCORING_RECORDING_PATH))
        return run_fit_scoring_evals(lambda e, raw, p: score_fit(e, raw, p, client=client))
    if suite == "routing":
        client = ReplayClient(load_recording(ROUTING_RECORDING_PATH))
        return run_routing_evals(lambda app, h: route_followup(app, h, client=client))
    client = ReplayClient(load_recording(COMPOSITION_RECORDING_PATH))
    return run_composition_evals(
        lambda app, h, intent, samples: compose_followup(app, h, intent, samples, client=client)
    )


@pytest.mark.parametrize("suite", sorted(PUBLISHED))
def test_recordings_replay_to_the_published_result(suite: str) -> None:
    summary = _replay(suite)
    passed = sum(r.passed for r in summary.results)
    errors = [r.case_id for r in summary.results if r.error]
    assert errors == [], f"{suite}: recordings no longer match these prompts: {errors}"
    assert (passed, len(summary.results)) == PUBLISHED[suite]


# ---- routing: which cases pass, by group, and what the model alone did -------------


def _routing_results(monkeypatch: pytest.MonkeyPatch | None = None, *, guard: bool = True):
    if not guard:
        assert monkeypatch is not None
        monkeypatch.setattr("jscc.routing._overturn_if_steered", lambda decision, history: decision)
    return {r.case_id: r for r in _replay("routing").results}


def test_routing_results_by_group_and_the_one_miss() -> None:
    """The published breakdown: core 25/26, held_out 10/10, hostile 2/2, and the one
    miss is a routine case sent to a person (the safe direction)."""
    results = _routing_results()
    by_group: dict[str, list[bool]] = {}
    for r in results.values():
        by_group.setdefault(r.group, []).append(r.passed)
    assert {g: (sum(v), len(v)) for g, v in by_group.items()} == {
        "core": (25, 26),
        "held_out": (10, 10),
        "hostile": (2, 2),
    }
    assert [cid for cid, r in results.items() if not r.passed] == ["routine-recruiter-ack"]


def test_the_router_model_alone_answered_routine_on_the_hostile_posting_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the code check off, the recorded model reply to the injected posting excerpt is
    a false-routine. The published 37/38 counts the check in `route_followup`, which
    overturns it; this pins that the model did not get there by itself."""
    results = _routing_results(monkeypatch, guard=False)
    assert not results["hostile-posting-excerpt-overrides-rules"].passed
    assert sum(r.passed for r in results.values()) == 36
