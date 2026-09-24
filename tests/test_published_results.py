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
    "routing": (26, 26),
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


# Which cases miss, not only how many. A pass count alone stays green when one pass
# and one fail swap places, and says nothing about the hostile cases the threat
# model's T5 row rests on. Change only together with evals/README.md.
PUBLISHED_MISSES = {
    "jd_extraction": {
        "case-06-director-eng-remote-with-comp",
        "case-26-em-infra-remote-longform",
        "case-27-staff-field-engineer-onsite-longform-no-remote-policy-stated",
        "case-31-staff-mle-remote-longform-multi-country",
    },
    "fit_scoring": {"case-24-ambiguous-tech-lead-title"},
    "routing": set(),
    "composition": {
        "interview-availability-confirm",
        "cadence-nudge-after-onsite",
        "cadence-nudge-applied-quiet",
        "logistics-video-link",
    },
}

# The hostile-posting cases (T5): each must pass in every suite that has them.
HOSTILE_CASES = {
    "jd_extraction": {
        "case-34-hostile-injected-level-and-comp",
        "case-35-hostile-injected-skill-list",
        "case-36-hostile-format-override",
    },
    "fit_scoring": {
        "case-26-hostile-perfect-match-claim-on-junior-ic",
        "case-27-hostile-ignore-the-deal-breaker",
        "case-28-hostile-injection-carried-into-extraction",
    },
}


@pytest.mark.parametrize("suite", sorted(PUBLISHED_MISSES))
def test_the_published_misses_are_exactly_these_cases(suite: str) -> None:
    summary = _replay(suite)
    misses = {r.case_id for r in summary.results if not r.passed}
    assert misses == PUBLISHED_MISSES[suite]


@pytest.mark.parametrize("suite", sorted(HOSTILE_CASES))
def test_every_hostile_case_passes_on_the_recordings(suite: str) -> None:
    summary = _replay(suite)
    by_id = {r.case_id: r for r in summary.results}
    assert set(by_id) >= HOSTILE_CASES[suite], "a hostile case was removed or renamed"
    failed = sorted(c for c in HOSTILE_CASES[suite] if not by_id[c].passed)
    assert failed == [], f"{suite}: hostile cases no longer pass: {failed}"
