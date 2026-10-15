"""The feature ledger runner: the rules `check` enforces and the one path to `passing`.

These pin the control itself. If `verify` could be skipped or a hand-edited
`passing` could slip past `check`, the ledger would stop meaning anything, so
each rule gets a test that fails when the rule is removed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import features  # noqa: E402

OK = "python -c pass"
FAIL = 'python -c "import sys; sys.exit(3)"'


def _feature(fid: str, state: str = "not_started", verify: str = OK) -> dict:
    return {"id": fid, "behavior": f"{fid} works", "verify": verify, "state": state}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point the module at a throwaway FEATURES.json/PROGRESS.md and fake git."""
    monkeypatch.setattr(features, "ROOT", tmp_path)
    monkeypatch.setattr(features, "FEATURES", tmp_path / "FEATURES.json")
    monkeypatch.setattr(features, "PROGRESS", tmp_path / "PROGRESS.md")
    monkeypatch.setattr(features, "git", _fake_git())

    def write(feats, **extra):
        data = {"version": 1, "features": feats, **extra}
        features.save(data)
        return data

    return write


def _fake_git(status="", shallow="false", known_commit="cafebabe" * 5):
    def git(*args):
        if args[:2] == ("rev-parse", "HEAD"):
            return known_commit
        if args[:2] == ("rev-parse", "--short"):
            return known_commit[:7]
        if args[:2] == ("rev-parse", "--is-shallow-repository"):
            return shallow
        if args[:1] == ("status",):
            return status
        if args[:2] == ("cat-file", "-t"):
            return "commit" if args[2] == known_commit else ""
        return ""

    return git


def _passing(fid: str, ledger_commit="cafebabe" * 5) -> dict:
    f = _feature(fid, "passing")
    ev = {"commit": ledger_commit, "dirty": False, "at": "2026-01-01T00:00:00Z", "exit_code": 0}
    ev["seal"] = features.seal(f, ev)
    f["evidence"] = ev
    return f


def test_seal_has_no_digits_and_depends_on_every_input():
    f = _feature("a", "passing")
    ev = {"commit": "abc", "at": "t"}
    s = features.seal(f, ev)
    assert len(s) == 16 and not any(c.isdigit() for c in s)
    assert features.seal({**f, "verify": "other"}, ev) != s
    assert features.seal(f, {**ev, "commit": "abd"}) != s
    assert features.seal_v1(f, ev) != s


def test_problems_accepts_sealed_passing_feature(ledger):
    data = ledger([_passing("a")])
    assert features.problems(data) == []


def test_problems_rejects_hand_edited_passing(ledger):
    data = ledger([_feature("a", "passing")])
    (msg,) = features.problems(data)
    assert "without evidence" in msg


def test_problems_rejects_tampered_seal(ledger):
    f = _passing("a")
    f["verify"] = "python -c 'print(1)'"  # command changed after sealing
    assert any("without evidence" in p for p in features.problems(ledger([f])))


def test_problems_accepts_legacy_v1_seal(ledger):
    f = _feature("a", "passing")
    ev = {"commit": "cafebabe" * 5, "at": "t", "dirty": False, "exit_code": 0}
    ev["seal"] = features.seal_v1(f, ev)
    f["evidence"] = ev
    assert features.problems(ledger([f])) == []


def test_problems_flags_wip_limit_duplicates_and_bad_state(ledger):
    data = ledger([_feature("a", "active"), _feature("b", "active"), _feature("a", "bogus")])
    joined = "\n".join(features.problems(data))
    assert "WIP limit is 1" in joined
    assert "duplicate feature ids" in joined
    assert "is not one of" in joined


def test_problems_flags_missing_fields(ledger):
    f = _feature("a")
    del f["verify"]
    assert any("missing 'verify'" in p for p in features.problems(ledger([f])))


def test_problems_flags_unknown_evidence_commit_but_skips_shallow(ledger, monkeypatch):
    data = ledger([_passing("a", ledger_commit="1" * 40)])
    assert any("not in this repo" in p for p in features.problems(data))
    monkeypatch.setattr(features, "git", _fake_git(shallow="true"))
    assert features.problems(data) == []


def test_activate_enforces_wip_limit_and_rejects_passing(ledger):
    data = ledger([_feature("a", "active"), _feature("b"), _passing("c")])
    with pytest.raises(SystemExit, match="WIP limit 1"):
        features.cmd_activate(data, "b")
    data = ledger([_feature("b"), _passing("c")])
    with pytest.raises(SystemExit, match="already passing"):
        features.cmd_activate(data, "c")
    features.cmd_activate(data, "b")
    assert features.load()["features"][0]["state"] == "active"


def test_find_unknown_id_exits(ledger):
    with pytest.raises(SystemExit, match="no feature with id"):
        features.find(ledger([_feature("a")]), "nope")


def test_verify_requires_active_state(ledger):
    data = ledger([_feature("a")])
    with pytest.raises(SystemExit, match="only an active feature"):
        features.cmd_verify(data, "a")


def test_verify_failure_leaves_state_unchanged(ledger):
    data = ledger([_feature("a", "active", verify=FAIL)])
    with pytest.raises(SystemExit, match="verification FAILED"):
        features.cmd_verify(data, "a")
    saved = features.load()["features"][0]
    assert saved["state"] == "active" and "evidence" not in saved


def test_verify_success_writes_sealed_evidence_that_check_accepts(ledger, capsys):
    data = ledger([_feature("a", "active")])
    features.cmd_verify(data, "a")
    saved = features.load()
    f = saved["features"][0]
    assert f["state"] == "passing" and f["evidence"]["exit_code"] == 0
    assert features.problems(saved) == []
    assert "PASSING" in capsys.readouterr().out


def test_verify_notes_dirty_tree(ledger, monkeypatch, capsys):
    monkeypatch.setattr(features, "git", _fake_git(status=" M x.py"))
    features.cmd_verify(ledger([_feature("a", "active")]), "a")
    assert "working tree was dirty" in capsys.readouterr().out


def test_verify_all_exits_nonzero_on_regression(ledger, capsys):
    ok, bad = _passing("ok"), _passing("bad")
    bad["verify"] = FAIL
    with pytest.raises(SystemExit) as e:
        features.cmd_verify_all(ledger([ok, bad]))
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "REGRESSION" in out and "bad" in out


def test_restamp_refuses_dirty_tree(ledger, monkeypatch):
    monkeypatch.setattr(features, "git", _fake_git(status=" M x.py"))
    with pytest.raises(SystemExit, match="clean working tree"):
        features.cmd_restamp(ledger([_passing("a")]))


def test_restamp_rewrites_evidence_and_leaves_regressions_alone(ledger, capsys):
    good, bad = _passing("good"), _passing("bad")
    bad["verify"] = FAIL
    bad["evidence"]["seal"] = features.seal(bad, bad["evidence"])
    old_bad = dict(bad["evidence"])
    with pytest.raises(SystemExit) as e:
        features.cmd_restamp(ledger([good, bad]))
    assert e.value.code == 1
    saved = {f["id"]: f for f in features.load()["features"]}
    assert saved["bad"]["evidence"] == old_bad
    assert saved["good"]["evidence"]["at"] != "2026-01-01T00:00:00Z"
    assert saved["good"]["state"] == "passing"


def test_block_records_reason_with_default(ledger):
    data = ledger([_feature("a", "active")])
    features.cmd_block(data, "a", "")
    f = features.load()["features"][0]
    assert f["state"] == "blocked" and f["blocked_reason"] == "no reason given"


def test_check_exit_code_follows_problems(ledger, capsys):
    with pytest.raises(SystemExit) as e:
        features.cmd_check(ledger([_feature("a", "passing")]))
    assert e.value.code == 1
    with pytest.raises(SystemExit) as e:
        features.cmd_check(ledger([_passing("a")]))
    assert e.value.code == 0
    assert "features: ok" in capsys.readouterr().out


def test_status_reports_completion_rate_or_na(ledger, capsys):
    features.cmd_status(ledger([_feature("a")]))
    assert "n/a" in capsys.readouterr().out
    features.cmd_status(ledger([_passing("a"), _feature("b", "active")]))
    assert "1/2 = 0.50" in capsys.readouterr().out


def test_progress_lists_every_state_with_done_when(ledger):
    data = ledger(
        [
            _passing("p"),
            _feature("act", "active"),
            {**_feature("blk", "blocked"), "blocked_reason": "waiting"},
            _feature("todo"),
        ],
        check_command="make check",
    )
    features.cmd_progress(data)
    text = features.PROGRESS.read_text(encoding="utf-8")
    assert "Full check: `make check`" in text
    assert "(verified at `cafebab`)" in text
    assert "Blocked: waiting" in text
    assert "done when: `python -c pass`" in text


def test_clean_fails_on_invalid_ledger_and_debug_leftover(ledger, monkeypatch, capsys):
    data = ledger([_feature("a", "passing")], check_command=OK)
    with pytest.raises(SystemExit) as e:
        features.cmd_clean(data)
    assert e.value.code == 1
    assert "NOT CLEAN" in capsys.readouterr().out

    base = _fake_git()
    marker = "TO" + "DO"  # keep this source file free of its own debug marker
    monkeypatch.setattr(
        features,
        "git",
        lambda *a: f"+x = 1  # {marker}" if a[:1] == ("diff",) else base(*a),
    )
    with pytest.raises(SystemExit) as e:
        features.cmd_clean(ledger([_passing("a")], check_command=OK))
    assert e.value.code == 1
    assert "debug leftovers" in capsys.readouterr().out


def test_clean_passes_when_everything_is_ok(ledger, capsys):
    with pytest.raises(SystemExit) as e:
        features.cmd_clean(ledger([_passing("a")], check_command=OK))
    assert e.value.code == 0
    assert "\nCLEAN" in capsys.readouterr().out


def test_clean_reports_failing_full_check(ledger, capsys):
    with pytest.raises(SystemExit) as e:
        features.cmd_clean(ledger([_passing("a")], check_command=FAIL))
    assert e.value.code == 1
    assert "full check failed" in capsys.readouterr().out


def test_full_check_needs_command_and_propagates_exit(ledger):
    with pytest.raises(SystemExit, match="no check_command"):
        features.cmd_full_check(ledger([_feature("a")]))
    with pytest.raises(SystemExit) as e:
        features.cmd_full_check(ledger([_feature("a")], check_command=FAIL))
    assert e.value.code == 3


def test_main_dispatch_and_usage_errors(ledger, capsys):
    ledger([_feature("a")])
    with pytest.raises(SystemExit):
        features.main(["features.py"])
    with pytest.raises(SystemExit):
        features.main(["features.py", "no-such-command"])
    with pytest.raises(SystemExit, match="needs a feature id"):
        features.main(["features.py", "activate"])
    features.main(["features.py", "list"])
    assert "not_started" in capsys.readouterr().out
    features.main(["features.py", "activate", "a"])
    assert (
        json.loads(features.FEATURES.read_text(encoding="utf-8"))["features"][0]["state"]
        == "active"
    )


def test_env_prepends_local_bin_when_present(tmp_path, monkeypatch):
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert features.env()["PATH"].startswith(str(tmp_path / ".local" / "bin"))
