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
