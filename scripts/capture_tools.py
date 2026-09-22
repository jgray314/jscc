"""Manual-capture and proxy tooling for the eval suites.

A manual capture round means pasting each case's prompt into a Claude.ai chat and
recording the completion. A proxy round means a subagent answers the same prompts so
wording can be iterated cheaply first. Both need every case's exact prompt, so this
script builds them the same way the eval command does, then serves each step:

    python scripts/capture_tools.py prompts <suite>
    python scripts/capture_tools.py show <suite> <n>
    python scripts/capture_tools.py record <suite> <case_id> <completion_file>
    python scripts/capture_tools.py proxy-prep <suite> <tag> [--samples N]
    python scripts/capture_tools.py proxy-grade <suite> <tag>
    python scripts/capture_tools.py recapture-cost [<suite>...]

Suites: jd_extraction, fit_scoring, routing, composition.

`recapture-cost` answers "what would this edit cost?" before a capture round is
committed to: run it with a prompt, fixture or redaction change in the working tree and
it lists every case whose recording no longer matches. Recordings are keyed by a hash of
the exact prompt, so one word in a system prompt invalidates the whole suite.

`prompts` must be re-run after any prompt, fixture or redaction change, because the
recording key hashes the exact prompt text. `show` and `record` refuse to run against
prompts older than the current code, so a stale file cannot record under a dead key.

Provenance: only a completion from a real chat counts as evidence. `proxy-grade`
grades in memory and cannot write a recording, and `record` refuses any file under a
`proxy` directory. Work files live in `.capture/` (gitignored).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Runnable as a bare `python scripts/capture_tools.py ...` with no install step, the
# same bootstrap the other scripts use.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jscc import evals  # noqa: E402
from jscc.composition import compose_followup  # noqa: E402
from jscc.extraction import extract_jd  # noqa: E402
from jscc.llm_client import LLMResponse  # noqa: E402
from jscc.routing import route_followup  # noqa: E402
from jscc.scoring import score_fit  # noqa: E402

DEFAULT_WORK_DIR = REPO_ROOT / ".capture"
SUITES = ("jd_extraction", "fit_scoring", "routing", "composition")


class _PromptCapture:
    """Stands in for the model: records each prompt in call order, answers with nothing.

    An empty answer fails the stage's parser, which the suite runner counts as one failed
    case and moves on from, so the runner itself walks every case for us."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        self.calls.append({"model": model, "system": system, "user": user})
        return LLMResponse(
            text="", input_tokens=0, output_tokens=0, cost_usd=0.0, stop_reason="end_turn"
        )


def _case_ids(suite: str) -> list[str]:
    loaders: dict[str, Callable[[], list[Any]]] = {
        "jd_extraction": evals.load_cases,
        "fit_scoring": evals.load_fit_cases,
        "routing": evals.load_routing_cases,
        "composition": evals.load_composition_cases,
    }
    return [case.id for case in loaders[suite]()]


def recording_path(suite: str) -> Path:
    return {
        "jd_extraction": evals.JD_EXTRACTION_RECORDING_PATH,
        "fit_scoring": evals.FIT_SCORING_RECORDING_PATH,
        "routing": evals.ROUTING_RECORDING_PATH,
        "composition": evals.COMPOSITION_RECORDING_PATH,
    }[suite]


def _run_suite(suite: str, client: Any) -> Any:
    """Run one suite through the same call the eval command makes, with `client`."""
    if suite == "jd_extraction":
        return evals.run_jd_extraction_evals(lambda raw: extract_jd(raw, client=client))
    if suite == "fit_scoring":
        return evals.run_fit_scoring_evals(
            lambda extracted, raw, profile: score_fit(extracted, raw, profile, client=client)
        )
    if suite == "routing":
        return evals.run_routing_evals(
            lambda app, history: route_followup(app, history, client=client)
        )
    return evals.run_composition_evals(
        lambda app, history, intent, styles: compose_followup(
            app, history, intent, styles, client=client
        )
    )


def build_prompts(suite: str) -> dict[str, dict[str, str]]:
    """Every case's exact model, system and user text, keyed by case id."""
    capture = _PromptCapture()
    _run_suite(suite, capture)
    ids = _case_ids(suite)
    if len(capture.calls) != len(ids):
        raise SystemExit(
            f"{suite}: {len(ids)} cases but {len(capture.calls)} model calls. A case failed "
            "before reaching the model (a malformed fixture, or the sanitizer refused it); "
            "fix that first, because a missing prompt would shift every case after it."
        )
    return dict(zip(ids, capture.calls, strict=True))


def _prompts_file(work_dir: Path, suite: str) -> Path:
    return work_dir / f"{suite}_prompts.json"


def write_prompts(suite: str, work_dir: Path) -> dict[str, dict[str, str]]:
    prompts = build_prompts(suite)
    work_dir.mkdir(parents=True, exist_ok=True)
    _prompts_file(work_dir, suite).write_text(json.dumps(prompts, indent=2), encoding="utf-8")
    return prompts


def load_current_prompts(suite: str, work_dir: Path) -> dict[str, dict[str, str]]:
    """The saved prompts, refused if the code now builds different ones."""
    path = _prompts_file(work_dir, suite)
    if not path.exists():
        raise SystemExit(f"no prompts for {suite}; run: capture_tools.py prompts {suite}")
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved != build_prompts(suite):
        raise SystemExit(
            f"{path.name} is stale: the prompts, fixtures or redaction rules changed since it "
            f"was written. Re-run: capture_tools.py prompts {suite}"
        )
    return saved


def format_case(suite: str, n: int, case_id: str, total: int, prompt: dict[str, str]) -> str:
    """One case as the chat needs it. The model is stated first and again last, because
    capturing a round on the wrong model has already cost a full round once."""
    model = prompt["model"]
    return "\n".join(
        [
            f"CASE {n}/{total}  {suite}  {case_id}",
            f"MODEL: {model}   <-- use exactly this model in the chat, fresh chat per case",
            "",
            "--- SYSTEM PROMPT ---",
            prompt["system"],
            "",
            "--- USER MESSAGE ---",
            prompt["user"],
            "",
            f"MODEL: {model}",
        ]
    )


def record_completion(
    suite: str,
    case_id: str,
    completion_file: Path,
    prompts: dict[str, dict[str, str]],
    path: Path,
) -> str:
    """Save one real-chat completion under the key the eval's replay will look up."""
    if "proxy" in {part.lower() for part in completion_file.resolve().parts}:
        raise SystemExit(
            f"{completion_file} is under a proxy directory. Proxy output is advisory and is "
            "never recorded; only a completion from a real chat is evidence."
        )
    if case_id not in prompts:
        raise SystemExit(f"unknown {suite} case {case_id!r}")
    completion = completion_file.read_text(encoding="utf-8")
    # A pasted-then-saved file picks up one trailing newline that the interactive
    # capture's input loop never adds. Anything else is kept as the model wrote it.
    completion = completion.removesuffix("\n").removesuffix("\r")
    if not completion.strip():
        raise SystemExit(f"{completion_file} is empty; nothing to record")
    p = prompts[case_id]
    key = evals._prompt_key(p["model"], p["system"], p["user"])
    evals.save_recording({key: completion}, path)
    return key


def prep_proxy(suite: str, tag: str, samples: int, work_dir: Path) -> Path:
    """One input file per case per sample, for a fresh subagent each to answer."""
    prompts = load_current_prompts(suite, work_dir)
    base = work_dir / "proxy" / suite / tag
    (base / "in").mkdir(parents=True, exist_ok=True)
    (base / "out").mkdir(parents=True, exist_ok=True)
    for case_id, p in prompts.items():
        for k in range(samples):
            (base / "in" / f"{case_id}__{k}.txt").write_text(
                f"=== SYSTEM PROMPT ===\n{p['system']}\n=== USER MESSAGE ===\n{p['user']}\n",
                encoding="utf-8",
            )
    return base


def grade_proxy(suite: str, tag: str, work_dir: Path) -> str:
    """Grade the proxy answers with the real grader, in memory. Writes nothing."""
    prompts = load_current_prompts(suite, work_dir)
    out_dir = work_dir / "proxy" / suite / tag / "out"
    by_sample: dict[int, dict[str, str]] = defaultdict(dict)
    for f in sorted(out_dir.glob("*.json")):
        case_id, k = f.stem.rsplit("__", 1)
        by_sample[int(k)][case_id] = f.read_text(encoding="utf-8").strip()
    if not by_sample:
        raise SystemExit(f"no answers in {out_dir}")

    lines: list[str] = []
    for k in sorted(by_sample):
        recorded = {
            evals._prompt_key(prompts[c]["model"], prompts[c]["system"], prompts[c]["user"]): text
            for c, text in by_sample[k].items()
        }
        summary = _run_suite(suite, evals.ReplayClient(recorded, suite=suite))
        answered = set(by_sample[k])
        missing = len(prompts) - len(answered)
        lines.append(
            f"sample {k}: {summary.passed}/{summary.total} ({summary.pass_rate:.0%}) "
            f"unanswered={missing}"
        )
        for r in summary.results:
            if r.passed or r.case_id not in answered:
                continue
            detail = r.error or "; ".join(f"{d.field}: {str(d.actual)[:100]}" for d in r.diffs)
            lines.append(f"   FAIL {r.case_id}: {detail}")
        if suite == "routing":
            lines.append(f"   false-routine: {evals.false_routine_cases(summary) or 'none'}")
        if suite == "composition":
            lines.append(
                f"   drafted instead of asking: {evals.drafted_instead_of_asking(summary) or 'none'}"
            )
    lines.append("ADVISORY ONLY: a proxy is a lower bound on failures, never evidence.")
    return "\n".join(lines)


def recapture_cost(suite: str, path: Path | None = None) -> tuple[list[str], int]:
    """Cases with no recording under the prompts the current code builds, and how many
    recorded keys no current case maps to (dead weight a recapture would replace)."""
    prompts = build_prompts(suite)
    recorded = evals.load_recording(path or recording_path(suite))
    keys = {
        case_id: evals._prompt_key(p["model"], p["system"], p["user"])
        for case_id, p in prompts.items()
    }
    missing = [case_id for case_id, key in keys.items() if key not in recorded]
    orphaned = len(set(recorded) - set(keys.values()))
    return missing, orphaned


def format_recapture_cost(suite: str, missing: list[str], orphaned: int, total: int) -> str:
    head = f"{suite}: {len(missing)}/{total} cases need a new capture"
    if orphaned:
        head += f"; {orphaned} recorded keys match no current case"
    return "\n".join([head, *(f"   {case_id}" for case_id in missing)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prompts", help="build every case's prompt from the current code")
    p.add_argument("suite", choices=SUITES)

    p = sub.add_parser("show", help="print case N (1-based) for the chat")
    p.add_argument("suite", choices=SUITES)
    p.add_argument("n", type=int)

    p = sub.add_parser("record", help="record one real-chat completion")
    p.add_argument("suite", choices=SUITES)
    p.add_argument("case_id")
    p.add_argument("completion_file", type=Path)

    p = sub.add_parser("proxy-prep", help="write proxy input files")
    p.add_argument("suite", choices=SUITES)
    p.add_argument("tag")
    p.add_argument("--samples", type=int, default=1)

    p = sub.add_parser("proxy-grade", help="grade proxy answers with the real grader")
    p.add_argument("suite", choices=SUITES)
    p.add_argument("tag")

    p = sub.add_parser(
        "recapture-cost", help="list cases whose recording the current code would not replay"
    )
    p.add_argument("suites", nargs="*", choices=SUITES, metavar="suite")

    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "prompts":
        prompts = write_prompts(args.suite, args.work_dir)
        models = sorted({p["model"] for p in prompts.values()})
        print(f"{len(prompts)} {args.suite} prompts, model {', '.join(models)}")
    elif args.cmd == "show":
        prompts = load_current_prompts(args.suite, args.work_dir)
        ids = list(prompts)
        if not 1 <= args.n <= len(ids):
            raise SystemExit(f"n must be 1..{len(ids)}")
        cid = ids[args.n - 1]
        print(format_case(args.suite, args.n, cid, len(ids), prompts[cid]))
    elif args.cmd == "record":
        prompts = load_current_prompts(args.suite, args.work_dir)
        key = record_completion(
            args.suite, args.case_id, args.completion_file, prompts, recording_path(args.suite)
        )
        print(f"recorded {args.case_id} -> {key[:20]}...")
    elif args.cmd == "proxy-prep":
        base = prep_proxy(args.suite, args.tag, args.samples, args.work_dir)
        print(f"inputs in {base / 'in'}; write each answer to {base / 'out'}/<case>__<k>.json")
    elif args.cmd == "recapture-cost":
        for suite in args.suites or SUITES:
            missing, orphaned = recapture_cost(suite)
            print(format_recapture_cost(suite, missing, orphaned, len(_case_ids(suite))))
    else:
        print(grade_proxy(args.suite, args.tag, args.work_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
