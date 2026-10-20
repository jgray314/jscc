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

Initialization is its own phase. A scaffolded FEATURES.json carries
`"init": {"state": "pending"}`; while pending, `activate` refuses and `check`
fails if any feature is active, blocked or passing. `init-check` verifies the
setup (placeholders filled, hooks wired, runtime pinned and matched in CI,
lockfile, CI workflow, enough features, full check green); `init-check --complete
--fresh-session-tested` records a sealed checkpoint at a clean HEAD and lifts the
gate. A FEATURES.json with no `init` key is a legacy project and is not gated.

Every command appends one line to .harness/events.jsonl (gitignored; command, feature id, ok),
as do the commit-msg hook (via `event`) and the subagent guard. That file is how
/harness-audit shows whether a control fired. A write failure is swallowed.

Usage: features.py {list,status,activate,verify,verify-all,block,check,progress,clean,full-check,restamp,init-check} [id] [reason]
       features.py event <source> <name> [key=value ...]
       features.py init-check [--complete] [--fresh-session-tested] [--skip-run]
"""

import hashlib
import json
import os
import re
import string
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEATURES = ROOT / "FEATURES.json"
PROGRESS = ROOT / "PROGRESS.md"
EVENTS = ROOT / ".harness" / "events.jsonl"
STATES = ("not_started", "active", "blocked", "passing")
DEBUG_PATTERNS = re.compile(
    r"console\.log|debugger\b|breakpoint\(\)|import pdb|pdb\.set_trace|\bTODO\b|\bFIXME\b"
)


def log_event(source, event, **fields):
    """Append one line to .harness/events.jsonl (gitignored). Never raises: a
    broken log must not break the control it observes."""
    try:
        rec = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": source,
            "event": event,
        }
        rec.update({k: v for k, v in fields.items() if v is not None})
        EVENTS.parent.mkdir(exist_ok=True)
        with EVENTS.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
    phone number to a content scanner (it did, once). Mapping the ten digits to g-p keeps
    the same entropy and removes every digit."""
    digest = hashlib.sha256(_seal_raw(f, ev, "v2").encode()).hexdigest()[:16]
    return digest.translate(str.maketrans(string.digits, "ghijklmnop"))


def seal_v1(f, ev):
    """The original hex seal. Still accepted by `check` so existing evidence stays
    valid until a restamp rewrites it; remove once every repo has restamped."""
    return hashlib.sha256(_seal_raw(f, ev, "v1").encode()).hexdigest()[:16]


def init_seal(rec):
    """Letters only, same reasoning as `seal`."""
    raw = f"init|{rec['commit']}|{rec['at']}|features.py-init-v1"
    return (
        hashlib.sha256(raw.encode())
        .hexdigest()[:16]
        .translate(str.maketrans(string.digits, "ghijklmnop"))
    )


def init_state(data):
    """None for a legacy project (no `init` key), else the recorded state."""
    init = data.get("init")
    return None if init is None else (init.get("state") if isinstance(init, dict) else "invalid")


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
    istate = init_state(data)
    if istate == "pending":
        started = [f["id"] for f in data["features"] if f.get("state") != "not_started"]
        if started:
            out.append(
                f"init is pending but {', '.join(started)} moved past not_started "
                "(finish `init-check --complete` before any feature work)"
            )
    elif istate == "complete":
        rec = data["init"]
        sealed = bool(rec.get("commit") and rec.get("at")) and rec.get("seal") == init_seal(rec)
        if not sealed:
            out.append("init: marked complete without a sealed record from `init-check --complete`")
        elif git("rev-parse", "--is-shallow-repository") != "true" and not git(
            "cat-file", "-t", rec["commit"]
        ):
            out.append(f"init: checkpoint commit {rec['commit'][:7]} not in this repo")
    elif istate is not None:
        out.append(f"init: state '{istate}' is not 'pending' or 'complete'")
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
    if init_state(data) == "pending":
        sys.exit(
            f"cannot activate {fid}: initialization is not complete. Work through "
            "INIT-CHECKLIST.md, then `python scripts/features.py init-check --complete "
            "--fresh-session-tested`."
        )
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
    if f["state"] == "passing":
        # Passing only changes through evidence. A passing feature that broke is a
        # regression, and blocking it would hide that instead of surfacing it.
        sys.exit(
            f"{fid} is passing; block only parks active or not-started work. "
            "If it broke, run `python scripts/features.py verify-all` to see the "
            "regression, then fix the code until it passes again."
        )
    f["state"], f["blocked_reason"] = "blocked", reason or "no reason given"
    save(data)
    print(f"{fid} blocked: {f['blocked_reason']}")


def _read(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def _first_existing(*names):
    return next((n for n in names if (ROOT / n).is_file()), None)


def _version_in_ci(pin_file, key, parts, workflows):
    """None when there is nothing to compare, else (ok, detail)."""
    pin = _read(pin_file)
    mentioned = [w for w in workflows if key in w]
    if pin is None or not mentioned:
        return None
    version = pin.split()[0].lstrip("v") if pin.split() else ""
    short = ".".join(version.split(".")[:parts])
    ok = bool(short) and all(short in w for w in mentioned)
    return ok, f"{pin_file} says {short or '?'}; CI '{key}' must name the same"


def init_checks(data):
    """Static setup checks: a list of (ok, label, hint). The full check runs separately."""
    res = []

    def add(ok, label, hint):
        res.append((bool(ok), label, hint))

    holders = [n for n in ("AGENTS.md", "FEATURES.json") if "{{" in (_read(n) or "")]
    add(not holders, "no {{PLACEHOLDERS}} left", f"fill them in: {', '.join(holders)}")
    agents = _read("AGENTS.md")
    add(
        agents is not None and len(agents.splitlines()) <= 100,
        "AGENTS.md exists and is 100 lines or fewer",
        "keep it short; link topic docs instead of inlining them",
    )
    hooks_path = git("config", "--get", "core.hooksPath")
    hooks = all((ROOT / ".githooks" / h).is_file() for h in ("commit-msg", "pre-push"))
    add(
        hooks and hooks_path == ".githooks",
        "commit-msg and pre-push hooks present, core.hooksPath=.githooks",
        "run: git config core.hooksPath .githooks",
    )
    add(
        "features.py check" in (_read(".githooks/pre-push") or ""),
        "pre-push runs the feature check",
        "add `python scripts/features.py check` to .githooks/pre-push",
    )
    add(
        "eol=lf" in (_read(".gitattributes") or ""),
        ".gitattributes pins eol=lf",
        "add `* text=auto eol=lf`",
    )
    pin = _first_existing(".python-version", ".nvmrc", ".node-version", ".tool-versions")
    engines = '"engines"' in (_read("package.json") or "")
    add(
        pin or engines,
        "runtime version pinned",
        "add .python-version, .nvmrc or package.json engines",
    )
    locks = (
        "uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json",
        "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum",
    )  # fmt: skip
    has_lock = _first_existing(*locks) or any(ROOT.glob("requirements*.txt"))
    add(has_lock, "dependencies locked", "commit a lockfile (or pinned requirements)")
    wf = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    texts = [t.read_text(encoding="utf-8", errors="replace") for t in wf]
    add(texts, "CI workflow present", "add .github/workflows/*.yml")
    add(
        any("features.py check" in t for t in texts),
        "CI runs the feature check",
        "add `python scripts/features.py check` to the CI workflow",
    )
    for pin_file, key, parts in (
        (".python-version", "python-version", 2),
        (".nvmrc", "node-version", 1),
    ):
        got = _version_in_ci(pin_file, key, parts, texts)
        if got:
            add(got[0], f"CI runtime matches {pin_file}", got[1])
    feats = data["features"]
    add(len(feats) >= 3, "at least three features", "add gates: tests, lint, format")
    add(bool(data.get("check_command")), "check_command set", "set check_command in FEATURES.json")
    return res


def cmd_init_check(data, flags):
    unknown = set(flags) - {"--complete", "--fresh-session-tested", "--skip-run"}
    if unknown:
        sys.exit(f"unknown flags: {', '.join(sorted(unknown))}\n{__doc__}")
    complete = "--complete" in flags
    if complete and "--skip-run" in flags:
        sys.exit("--complete needs the real full check; drop --skip-run")
    state = init_state(data)
    if state is None:
        sys.exit("legacy project: FEATURES.json has no `init` key, nothing to gate")
    if state == "complete" and not complete:
        print("init: already complete (re-running checks)")
    results = init_checks(data)
    for ok, label, hint in results:
        print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  -> {hint}"))
    failed = [label for ok, label, _ in results if not ok]
    if "--skip-run" not in flags and data.get("check_command"):
        r = sh(data["check_command"])
        ok = r.returncode == 0
        print(f"{'ok  ' if ok else 'FAIL'} full check passes ({data['check_command']})")
        if not ok:
            failed.append("full check")
            tail = (r.stdout + r.stderr).strip().splitlines()[-10:]
            print("\n".join("     " + ln for ln in tail))
    if not complete:
        print(
            "\nStill manual: open a fresh session with only the repo and have it answer what "
            "this is, how to run it, how to verify it, and where progress stands."
        )
        sys.exit(1 if failed else 0)
    if failed:
        sys.exit(f"\ninit NOT complete: {len(failed)} check(s) failing")
    if "--fresh-session-tested" not in flags:
        sys.exit("\nrun the fresh-session test, then pass --fresh-session-tested")
    if not git("rev-parse", "--verify", "HEAD"):
        sys.exit("\ncommit the setup first: the checkpoint is a commit")
    if git("status", "--porcelain"):
        sys.exit("\nworking tree is dirty: commit the setup, then run --complete on a clean tree")
    rec = {
        "state": "complete",
        "commit": git("rev-parse", "HEAD"),
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fresh_session_tested": True,
    }
    rec["seal"] = init_seal(rec)
    data["init"] = rec
    save(data)
    print(
        f"\ninit COMPLETE at {rec['commit'][:7]}. Commit FEATURES.json; features can be activated."
    )


def cmd_check(data):
    probs = problems(data)
    for p in probs:
        print("features:", p)
    if init_state(data) == "pending":
        print("features: init phase pending (no feature work until `init-check --complete`)")
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
        "This is the verification ledger: check gates and controls, each with a command that proves it.",
        "It is not the product roadmap. What the product does and what is open lives in the README",
        "and plan docs that AGENTS.md lists under 'Where things are'.",
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


def ci_note(repo):
    """One line on the latest GitHub Actions run for the upstream commit. Advisory:
    never fails the session, and any error (offline, private repo) reads as skipped."""
    if not repo:
        return "skipped: no ci_repo in FEATURES.json"
    try:
        sha = git("rev-parse", "@" + "{u}")
        if not sha:
            return "skipped: no upstream branch"
        url = f"https://api.github.com/repos/{repo}/actions/runs?head_sha={sha}&per_page=1"
        resp = subprocess.run(
            ["curl", "-sf", "--max-time", "10", url], capture_output=True, text=True, timeout=15
        )  # curl, not urllib: jscc's SSRF registry rejects in-process fetches
        runs = json.loads(resp.stdout)["workflow_runs"]
        if not runs:
            return f"WARN no CI run found for pushed commit {sha[:7]}"
        run = runs[0]
        state = run["conclusion"] or run["status"]
        return ("ok" if state == "success" else f"WARN {state}") + f" ({sha[:7]})"
    except Exception as e:
        return f"skipped: {type(e).__name__}"


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
    print("3. standard startup path")
    sc = data.get("startup_command")
    if not sc:
        print("   skipped: no startup_command in FEATURES.json")
    else:
        r = sh(sc)
        if r.returncode:
            failed.append("startup path failed")
            print(
                "   FAIL\n"
                + "\n".join("   " + ln for ln in (r.stdout + r.stderr).strip().splitlines()[-15:])
            )
        else:
            print("   ok")
    print("4. CI on the pushed commit")
    print("   " + ci_note(data.get("ci_repo")))
    print("5. debug leftovers in uncommitted and unpushed changes")
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
    print("6. working tree")
    dirty = git("status", "--porcelain")
    print(
        "   WARN uncommitted changes:\n" + "\n".join("   " + ln for ln in dirty.splitlines()[:10])
        if dirty
        else "   ok"
    )
    print("7. extra worktrees")
    wts = [ln for ln in git("worktree", "list").splitlines()][1:]
    print("   WARN " + "; ".join(wts) if wts else "   ok")
    print("\nCLEAN" if not failed else "\nNOT CLEAN: " + ", ".join(failed))
    log_event(
        "features",
        "clean-result",
        failed=", ".join(failed) or None,
        uncommitted=bool(dirty),
        worktrees=len(wts),
    )
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
    if cmd == "event":  # for shell hooks: event <source> <name> [key=value ...]
        if len(rest) < 2:
            sys.exit(__doc__)
        log_event(rest[0], rest[1], **dict(kv.split("=", 1) for kv in rest[2:] if "=" in kv))
        return
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
        "init-check": lambda: cmd_init_check(data, rest),
    }
    if cmd not in table:
        sys.exit(__doc__)
    if cmd in ("activate", "verify", "block") and not rest:
        sys.exit(f"{cmd} needs a feature id")
    code = 0
    try:
        table[cmd]()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        raise
    finally:
        log_event(
            "features",
            cmd,
            id=rest[0] if cmd in ("activate", "verify", "block") else None,
            ok=code == 0,
        )


if __name__ == "__main__":
    main(sys.argv)
