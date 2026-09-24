from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import capture_tools  # noqa: E402

from jscc import evals  # noqa: E402

# A well-formed completion for each stage, so replaying it exercises the real parser.
_ROUTING_OK = '{"classification": "non_routine", "reason": "x", "considerations": ["y"]}'


@pytest.mark.parametrize("suite", capture_tools.SUITES)
def test_prompts_cover_every_case_in_fixture_order(suite: str) -> None:
    prompts = capture_tools.build_prompts(suite)
    assert list(prompts) == capture_tools._case_ids(suite)
    for p in prompts.values():
        assert p["model"] and p["system"] and p["user"]


def test_a_recorded_completion_is_found_by_the_evals_replay(tmp_path: Path) -> None:
    """The key `record` writes must be the one the suite's replay looks up. If the two
    drifted, a whole capture round would replay as 'no recorded response'."""
    prompts = capture_tools.build_prompts("routing")
    rec = tmp_path / "recorded.json"
    for case_id in prompts:
        f = tmp_path / f"{case_id}.txt"
        f.write_text(_ROUTING_OK + "\n", encoding="utf-8")
        capture_tools.record_completion("routing", case_id, f, prompts, rec)

    summary = capture_tools._run_suite(
        "routing", evals.ReplayClient(evals.load_recording(rec), suite="routing")
    )
    assert summary.total == len(prompts)
    assert not [r for r in summary.results if r.error and "no recorded response" in r.error]


def test_record_strips_one_trailing_newline_and_keeps_the_rest(tmp_path: Path) -> None:
    prompts = capture_tools.build_prompts("routing")
    case_id = next(iter(prompts))
    f = tmp_path / "c.txt"
    f.write_text("line one\n\nline two\n", encoding="utf-8")
    rec = tmp_path / "recorded.json"
    key = capture_tools.record_completion("routing", case_id, f, prompts, rec)
    assert evals.load_recording(rec)[key] == "line one\n\nline two"


def test_record_refuses_a_file_under_a_proxy_directory(tmp_path: Path) -> None:
    prompts = capture_tools.build_prompts("routing")
    proxy_out = tmp_path / "proxy" / "routing" / "p1" / "out"
    proxy_out.mkdir(parents=True)
    f = proxy_out / "x__0.json"
    f.write_text(_ROUTING_OK, encoding="utf-8")
    rec = tmp_path / "recorded.json"
    with pytest.raises(SystemExit, match="proxy"):
        capture_tools.record_completion("routing", next(iter(prompts)), f, prompts, rec)
    assert not rec.exists()


def test_record_refuses_unknown_case_and_empty_file(tmp_path: Path) -> None:
    prompts = capture_tools.build_prompts("routing")
    f = tmp_path / "c.txt"
    f.write_text("ok", encoding="utf-8")
    with pytest.raises(SystemExit, match="unknown"):
        capture_tools.record_completion("routing", "no-such-case", f, prompts, tmp_path / "r.json")
    f.write_text("\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="empty"):
        capture_tools.record_completion(
            "routing", next(iter(prompts)), f, prompts, tmp_path / "r.json"
        )


def test_stale_prompts_are_refused(tmp_path: Path) -> None:
    """A saved prompts file that no longer matches the code would record under a dead key."""
    capture_tools.write_prompts("routing", tmp_path)
    path = tmp_path / "routing_prompts.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    first = next(iter(saved))
    saved[first]["system"] += " (edited since)"
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(SystemExit, match="stale"):
        capture_tools.load_current_prompts("routing", tmp_path)


def test_show_states_the_model_first_and_last() -> None:
    prompts = capture_tools.build_prompts("routing")
    case_id = next(iter(prompts))
    lines = capture_tools.format_case(
        "routing", 1, case_id, len(prompts), prompts[case_id]
    ).splitlines()
    model = prompts[case_id]["model"]
    assert lines[1].startswith(f"MODEL: {model}")
    assert lines[-1] == f"MODEL: {model}"


def test_proxy_grade_reports_failures_and_writes_no_recording(tmp_path: Path) -> None:
    capture_tools.write_prompts("routing", tmp_path)
    base = capture_tools.prep_proxy("routing", "t1", 1, tmp_path)
    ids = capture_tools._case_ids("routing")
    for case_id in ids:
        (base / "out" / f"{case_id}__0.json").write_text(_ROUTING_OK, encoding="utf-8")

    real = capture_tools.recording_path("routing")
    before = real.read_bytes()
    report = capture_tools.grade_proxy("routing", "t1", tmp_path)
    assert real.read_bytes() == before
    assert "sample 0:" in report
    assert "false-routine: none" in report  # everything answered non_routine
    assert "ADVISORY ONLY" in report


@pytest.mark.parametrize("suite", capture_tools.SUITES)
def test_recapture_cost_is_zero_for_the_committed_recordings(suite: str) -> None:
    """Every committed suite replays in CI, so on a clean tree nothing needs capture."""
    missing, orphaned = capture_tools.recapture_cost(suite)
    assert missing == []
    assert orphaned == 0


def test_recapture_cost_lists_every_case_a_system_prompt_edit_invalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jscc.scoring

    monkeypatch.setattr(
        jscc.scoring, "SCORING_SYSTEM_PROMPT", jscc.scoring.SCORING_SYSTEM_PROMPT + " "
    )
    missing, orphaned = capture_tools.recapture_cost("fit_scoring")
    assert missing == capture_tools._case_ids("fit_scoring")
    assert orphaned == len(evals.load_recording(evals.FIT_SCORING_RECORDING_PATH))


def test_recapture_cost_counts_only_the_cases_a_fixture_edit_touches(tmp_path: Path) -> None:
    prompts = capture_tools.build_prompts("routing")
    rec = tmp_path / "recorded.json"
    ids = list(prompts)
    for case_id in ids[1:]:
        p = prompts[case_id]
        evals.save_recording({evals._prompt_key(p["model"], p["system"], p["user"]): "x"}, rec)
    evals.save_recording({"sha256:" + "0" * 64: "x"}, rec)
    missing, orphaned = capture_tools.recapture_cost("routing", rec)
    assert missing == [ids[0]]
    assert orphaned == 1
    out = capture_tools.format_recapture_cost("routing", missing, orphaned, len(ids))
    assert (
        out.splitlines()[0]
        == f"routing: 1/{len(ids)} cases need a new capture; 1 recorded keys match no current case"
    )


class _Clip:
    """A fake clipboard: what the chat's reply would be, and what next_case loaded."""

    def __init__(self) -> None:
        self.text = ""

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> None:
        self.text = text


def _next(tmp_path, clip, model=None):
    return capture_tools.next_case(
        "routing",
        tmp_path,
        model,
        read_clip=clip.read,
        write_clip=clip.write,
        path=tmp_path / "rec.json",
    )


def test_next_walks_the_round_recording_each_reply(tmp_path):
    prompts = capture_tools.write_prompts("routing", tmp_path)
    ids = list(prompts)
    clip = _Clip()
    assert "case 1/" in _next(tmp_path, clip, "haiku")
    assert clip.text.startswith(f"CASE 1/{len(ids)}")
    for i, cid in enumerate(ids[:3]):
        clip.text = _ROUTING_OK + f" {i}"
        status = _next(tmp_path, clip)
        assert f"recorded {cid}" in status
        assert clip.text.startswith(f"CASE {i + 2}/")
    recorded = evals.load_recording(tmp_path / "rec.json")
    assert len(recorded) == 3
    p = prompts[ids[1]]
    assert recorded[evals._prompt_key(p["model"], p["system"], p["user"])].endswith(" 1")


def test_next_needs_the_model_to_start_and_refuses_the_wrong_one(tmp_path):
    capture_tools.write_prompts("routing", tmp_path)
    with pytest.raises(SystemExit, match="which model"):
        _next(tmp_path, _Clip())
    with pytest.raises(SystemExit, match="not the model"):
        _next(tmp_path, _Clip(), "sonnet")


def test_next_refuses_a_clipboard_that_is_empty_or_still_the_prompt(tmp_path):
    capture_tools.write_prompts("routing", tmp_path)
    clip = _Clip()
    _next(tmp_path, clip, "haiku")
    with pytest.raises(SystemExit, match="still holds the prompt"):
        _next(tmp_path, clip)
    clip.text = "  \n"
    with pytest.raises(SystemExit, match="clipboard is empty"):
        _next(tmp_path, clip)
    assert not (tmp_path / "rec.json").exists()


def test_next_resumes_after_the_recordings_already_made(tmp_path):
    prompts = capture_tools.write_prompts("routing", tmp_path)
    first = next(iter(prompts))
    p = prompts[first]
    evals.save_recording(
        {evals._prompt_key(p["model"], p["system"], p["user"]): _ROUTING_OK}, tmp_path / "rec.json"
    )
    clip = _Clip()
    assert "case 2/" in _next(tmp_path, clip, "haiku")


def test_next_reports_a_complete_round(tmp_path):
    prompts = capture_tools.write_prompts("routing", tmp_path)
    recorded = {
        evals._prompt_key(p["model"], p["system"], p["user"]): _ROUTING_OK for p in prompts.values()
    }
    evals.save_recording(recorded, tmp_path / "rec.json")
    assert "Round complete" in _next(tmp_path, _Clip(), "haiku")
