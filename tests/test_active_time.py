"""The commit-gap estimator: the arithmetic, and that its caveats cannot be dropped.

The number this script produces is only defensible next to its stated bias, so
one test pins that the text and JSON outputs always carry the caveats.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import active_time  # noqa: E402

T0 = datetime(2026, 1, 5, 9, 0)


def _commit(minutes: int, subject: str = "Slice X: a feature") -> active_time.Commit:
    return active_time.Commit("abc1234", T0 + timedelta(minutes=minutes), subject)


def test_sums_gaps_under_the_cap() -> None:
    result = active_time.analyze([_commit(0), _commit(30), _commit(90)])
    assert result.total == timedelta(minutes=90)
    assert result.counted_gaps == 2
    assert result.clusters == 1


def test_a_gap_over_the_cap_is_dropped_and_starts_a_new_cluster() -> None:
    result = active_time.analyze(
        [_commit(0), _commit(30), _commit(30 + 5 * 60), _commit(30 + 5 * 60 + 10)]
    )
    assert result.total == timedelta(minutes=40)
    assert result.dropped_gaps == 1
    assert result.clusters == 2


def test_a_gap_exactly_at_the_cap_is_counted() -> None:
    result = active_time.analyze([_commit(0), _commit(240)], cap=timedelta(hours=4))
    assert result.total == timedelta(hours=4)
    assert result.dropped_gaps == 0


def test_input_order_does_not_matter() -> None:
    forward = active_time.analyze([_commit(0), _commit(20), _commit(50)])
    shuffled = active_time.analyze([_commit(50), _commit(0), _commit(20)])
    assert forward.total == shuffled.total


def test_time_is_credited_to_the_commit_that_ends_the_gap() -> None:
    result = active_time.analyze(
        [
            _commit(0),
            _commit(60, "Phase B -> C gate: fix H-6"),
            _commit(90, "Slice D1: routing eval suite"),
        ]
    )
    assert result.by_category[active_time.GATE] == timedelta(minutes=60)
    assert result.by_category[active_time.FEATURE] == timedelta(minutes=30)


def test_empty_and_single_commit_histories_are_zero_not_errors() -> None:
    assert active_time.analyze([]).total == timedelta()
    assert active_time.analyze([]).clusters == 0
    single = active_time.analyze([_commit(0)])
    assert single.total == timedelta()
    assert single.clusters == 1


@pytest.mark.parametrize(
    "subject, expected",
    [
        ("Phase C -> D gate: fix G1, DNS-rebinding SSRF bypass in fetcher", active_time.GATE),
        ("ci: bump astral-sh/setup-uv from v3 to v6", active_time.GATE),
        ("docs: changelog entry for B12", active_time.GATE),
        ("Slice C1: fit scoring eval suite", active_time.FEATURE),
        ("D2b: round 3 manual capture, 20/20 with zero false-routine", active_time.FEATURE),
    ],
)
def test_classifier_on_known_subjects(subject: str, expected: str) -> None:
    assert active_time.classify(subject) == expected


def test_parse_log_reads_the_git_format() -> None:
    commits = active_time.parse_log("a1b2c3d|2026-01-05T09:00:00-08:00|Slice A1: scaffold\n\n")
    assert len(commits) == 1
    assert commits[0].subject == "Slice A1: scaffold"


def test_subjects_containing_the_delimiter_survive_parsing() -> None:
    commits = active_time.parse_log("a1b2c3d|2026-01-05T09:00:00-08:00|fix: a | b")
    assert commits[0].subject == "fix: a | b"


def test_text_report_always_carries_the_caveats() -> None:
    result = active_time.analyze([_commit(0), _commit(30)])
    report = active_time.format_report(result, timedelta(hours=4))
    for caveat in active_time.CAVEATS:
        assert caveat in report


def test_json_output_always_carries_the_caveats(capsys: pytest.CaptureFixture[str]) -> None:
    assert active_time.main(["--json", "--repo", str(REPO_ROOT)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["caveats"] == list(active_time.CAVEATS)
    assert payload["active_hours_upper_bound"] >= 0
