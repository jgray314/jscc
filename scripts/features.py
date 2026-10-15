#!/usr/bin/env python3
"""Feature list runner: the harness, not the agent, decides what is done.

FEATURES.json is the scope surface for a project. Each feature has a behavior,
a verification command and a state. The only path to `passing` is `verify`,
which runs the command and records evidence. `check` is fast and runs in the
pre-push hook. Stdlib only, so it copies into any repo.

States: not_started, active, blocked, passing.
Rules enforced by `check`:
  - at most one feature is active (WIP limit 1)
  - a passing feature carries evidence written by this script (a seal over
    id, verify command, commit and time), so a hand edit to `passing` fails
  - the evidence commit exists in this repo

The seal is tamper-evidence against casual edits, not security: an agent that
reads this file could recompute it. The control is that hand edits are visible
in review and fail the hook, which turns "I marked it done" into a deliberate act.

Usage: features.py {list,status,activate,verify,verify-all,block,check,progress,clean,full-check,restamp} [id] [reason]
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEATURES = ROOT / "FEATURES.json"
PROGRESS = ROOT / "PROGRESS.md"
STATES = ("not_started", "active", "blocked", "passing")
DEBUG_PATTERNS = re.compile(
    r"console\.log|debugger\b|breakpoint\(\)|import pdb|pdb\.set_trace|\bTODO\b|\bFIXME\b"
)


def env():
    e = dict(os.environ)
    local = Path.home() / ".local" / "bin"
    if local.exists():
        e["PATH"] = str(local) + os.pathsep + e.get("PATH", "")
    return e


def sh(cmd, capture=True):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=ROOT,
        env=env(),
        text=True,
        capture_output=capture,
        encoding="utf-8",
        errors="replace",
    )


def git(*args):
    r = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace"
    )
    return r.stdout.strip()


def load():
    return json.loads(FEATURES.read_text(encoding="utf-8"))


def save(data):
    FEATURES.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def _seal_raw(f, ev, version):
    return f"{f['id']}|{f['verify']}|{ev['commit']}|{ev['at']}|features.py-{version}"


def seal(f, ev):
    """Letters only. A hex seal can contain a long enough digit run to read as a
    phone number to a content scanner (it did, once). Mapping 0-9 to g-p keeps
    the same entropy and removes every digit."""
    digest = hashlib.sha256(_seal_raw(f, ev, "v2").encode()).hexdigest()[:16]
    return digest.translate(str.maketrans("0123456789", "ghijklmnop"))


def seal_v1(f, ev):
    """The original hex seal. Still accepted by `check` so existing evidence stays
    valid until a restamp rewrites it; remove once every repo has restamped."""
    return hashlib.sha256(_seal_raw(f, ev, "v1").encode()).hexdigest()[:16]


def find(data, fid):
    for f in data["features"]:
        if f["id"] == fid:
            return f
    sys.exit(f"no feature with id '{fid}'")


def vcr(data):
    fs = data["features"]
    passing = sum(f["state"] == "passing" for f in fs)
    activated = passing + sum(f["state"] in ("active", "blocked") for f in fs)
    return passing, activated


def problems(data):
    out = []
    ids = [f.get("id") for f in data["features"]]
    if len(set(ids)) != len(ids):
        out.append("duplicate feature ids")
    active = [f["id"] for f in data["features"] if f.get("state") == "active"]
    if len(active) > 1:
        out.append(f"WIP limit is 1, active: {', '.join(active)}")
    for f in data["features"]:
        fid = f.get("id", "?")
        for key in ("id", "behavior", "verify", "state"):
            if not f.get(key):
                out.append(f"{fid}: missing '{key}'")
        if f.get("state") not in STATES:
            out.append(f"{fid}: state '{f.get('state')}' is not one of {STATES}")
        if f.get("state") == "passing":
            ev = f.get("evidence") or {}
            sealed = bool(ev.get("commit")) and ev.get("seal") in (seal(f, ev), seal_v1(f, ev))
            if not sealed:
                out.append(
                    f"{fid}: marked passing without evidence from features.py verify "
                    f"(run `python scripts/features.py verify {fid}` instead of editing state)"
                )
            elif git("rev-parse", "--is-shallow-repository") != "true" and not git(
                "cat-file", "-t", ev["commit"]
            ):
                # Skipped on a shallow clone (CI's default checkout): the commit
                # may exist upstream but not in the truncated history.
                out.append(f"{fid}: evidence commit {ev['commit'][:7]} not in this repo")
    return out


def cmd_list(data):
    for f in data["features"]:
        print(f"{f['state']:<12} {f['id']:<28} {f['behavior']}")


def cmd_status(data):
    cmd_list(data)
    p, a = vcr(data)
    rate = f"{p}/{a} = {p / a:.2f}" if a else "n/a (nothing activated)"
    print(f"\nVerified completion rate: {rate}")


def cmd_activate(data, fid):
    f = find(data, fid)
    busy = [
        x["id"] for x in data["features"] if x["state"] in ("active", "blocked") and x["id"] != fid
    ]
    if busy:
        sys.exit(
            f"cannot activate {fid}: {', '.join(busy)} still active or blocked "
            "(WIP limit 1; verified completion rate must be 1.0 before new work)"
        )
    if f["state"] == "passing":
        sys.exit(f"{fid} is already passing")
    f["state"] = "active"
    save(data)
    print(f"{fid} active. Done means: {f['verify']}")


def run_verify(f):
    r = sh(f["verify"])
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def cmd_verify(data, fid):
    f = find(data, fid)
    if f["state"] != "active":
        sys.exit(f"{fid} is {f['state']}; only an active feature can be verified into passing")
    code, out = run_verify(f)
    if code != 0:
        print("\n".join(out.strip().splitlines()[-25:]))
        sys.exit(
            f"\n{fid}: verification FAILED (exit {code}). State unchanged. "
            f"Fix the behavior, then re-run: python scripts/features.py verify {fid}"
        )
    ev = {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exit_code": 0,
    }
    ev["seal"] = seal(f, ev)
    f["state"], f["evidence"] = "passing", ev
    save(data)
    note = (
        " (working tree was dirty: commit before relying on this evidence)" if ev["dirty"] else ""
    )
    print(f"{fid}: PASSING at {ev['commit'][:7]}{note}")


def cmd_verify_all(data):
    bad = 0
    for f in data["features"]:
        if f["state"] != "passing":
            continue
        code, out = run_verify(f)
        status = "ok" if code == 0 else "REGRESSION"
        bad += code != 0
        print(f"{status:<11} {f['id']}")
        if code != 0:
            print("\n".join("    " + ln for ln in out.strip().splitlines()[-8:]))
    sys.exit(1 if bad else 0)


def cmd_restamp(data):
    """Re-run every passing feature at a clean HEAD and rewrite its evidence.

    Evidence recorded from a dirty tree names a commit that did not contain
    the code that passed. Restamp fixes that without changing any state: a
    feature that no longer passes is reported as a REGRESSION and left alone.
    Refuses on a dirty tree, since the point is evidence tied to a commit.
    """
    if git("status", "--porcelain"):
        sys.exit("restamp needs a clean working tree: commit first, then restamp")
    bad = 0
    for f in data["features"]:
        if f["state"] != "passing":
            continue
        code, out = run_verify(f)
        if code != 0:
            bad += 1
            print(f"REGRESSION  {f['id']}")
            print("\n".join("    " + ln for ln in out.strip().splitlines()[-8:]))
            continue
        ev = {
            "commit": git("rev-parse", "HEAD"),
            "dirty": False,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "exit_code": 0,
        }
        ev["seal"] = seal(f, ev)
        f["evidence"] = ev
        print(f"restamped   {f['id']} at {ev['commit'][:7]}")
    save(data)
    print("\nCommit FEATURES.json and PROGRESS.md; this is a bookkeeping commit, not a feature.")
    sys.exit(1 if bad else 0)


def cmd_block(data, fid, reason):
    f = find(data, fid)
    f["state"], f["blocked_reason"] = "blocked", reason or "no reason given"
    save(data)
    print(f"{fid} blocked: {f['blocked_reason']}")


def cmd_check(data):
    probs = problems(data)
    for p in probs:
        print("features:", p)
    if not probs:
        p, a = vcr(data)
        print(f"features: ok ({len(data['features'])} features, {p} passing, {a - p} in flight)")
    sys.exit(1 if probs else 0)


def cmd_progress(data):
    head = git("rev-parse", "--short", "HEAD")
    lines = [
        "# Progress",
        "",
        "Generated by `python scripts/features.py progress` from FEATURES.json. Do not edit by hand.",
        "",
        f"Commit: `{head}`  ",
        f"Full check: `{data.get('check_command', 'not defined')}`",
        "",
    ]
    names = {
        "passing": "Done",
        "active": "In progress",
        "blocked": "Blocked",
        "not_started": "Not started",
    }
    for state in ("passing", "active", "blocked", "not_started"):
        items = [f for f in data["features"] if f["state"] == state]
        lines += [f"## {names[state]}", ""]
        if not items:
            lines += ["None.", ""]
            continue
        for f in items:
            extra = ""
            if state == "passing":
                extra = f" (verified at `{f['evidence']['commit'][:7]}`)"
            if state == "blocked":
                extra = f" Blocked: {f.get('blocked_reason', '')}"
            lines.append(f"- **{f['id']}**: {f['behavior']}{extra}")
            if state != "passing":
                lines.append(f"  - done when: `{f['verify']}`")
        lines.append("")
    PROGRESS.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"wrote {PROGRESS.name}")


def cmd_clean(data):
    """End-of-session exit criteria: transaction-style commit or roll back."""
    failed = []
    print("1. feature list")
    if problems(data):
        failed.append("feature list invalid")
        for p in problems(data):
            print("   FAIL", p)
    else:
        print("   ok")
    print("2. full check")
    cc = data.get("check_command")
    if not cc:
        print("   skipped: no check_command in FEATURES.json")
    else:
        r = sh(cc)
        if r.returncode:
            failed.append("full check failed")
            print(
                "   FAIL\n"
                + "\n".join("   " + ln for ln in (r.stdout + r.stderr).strip().splitlines()[-15:])
            )
        else:
            print("   ok")
    print("3. debug leftovers in uncommitted and unpushed changes")
    diff = git("diff", "HEAD", "-U0") + "\n" + git("diff", "@" + "{u}..HEAD", "-U0")
    hits = [
        ln
        for ln in diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++") and DEBUG_PATTERNS.search(ln)
    ]
    if hits:
        failed.append("debug leftovers")
        for h in hits[:10]:
            print("   FAIL", h[:100])
    else:
        print("   ok")
    print("4. working tree")
    dirty = git("status", "--porcelain")
    print(
        "   WARN uncommitted changes:\n" + "\n".join("   " + ln for ln in dirty.splitlines()[:10])
        if dirty
        else "   ok"
    )
    print("5. extra worktrees")
    wts = [ln for ln in git("worktree", "list").splitlines()][1:]
    print("   WARN " + "; ".join(wts) if wts else "   ok")
    print("\nCLEAN" if not failed else "\nNOT CLEAN: " + ", ".join(failed))
    sys.exit(1 if failed else 0)


def cmd_full_check(data):
    """Run the project's full check command (the one CI runs). Used by pre-push."""
    cc = data.get("check_command")
    if not cc:
        sys.exit("no check_command in FEATURES.json")
    r = sh(cc, capture=False)
    sys.exit(r.returncode)


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd, rest = argv[1], argv[2:]
    data = load()
    table = {
        "list": lambda: cmd_list(data),
        "status": lambda: cmd_status(data),
        "activate": lambda: cmd_activate(data, rest[0]),
        "verify": lambda: cmd_verify(data, rest[0]),
        "verify-all": lambda: cmd_verify_all(data),
        "block": lambda: cmd_block(data, rest[0], " ".join(rest[1:])),
        "check": lambda: cmd_check(data),
        "progress": lambda: cmd_progress(data),
        "clean": lambda: cmd_clean(data),
        "full-check": lambda: cmd_full_check(data),
        "restamp": lambda: cmd_restamp(data),
    }
    if cmd not in table:
        sys.exit(__doc__)
    if cmd in ("activate", "verify", "block") and not rest:
        sys.exit(f"{cmd} needs a feature id")
    table[cmd]()


if __name__ == "__main__":
    main(sys.argv)
