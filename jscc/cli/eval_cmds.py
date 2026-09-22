"""`jscc eval <suite>`: one subcommand per eval suite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from ..composition import COMPOSITION_EVAL_FEATURE, compose_followup
from ..evals import (
    COMPOSITION_PASS_THRESHOLD,
    COMPOSITION_RECORDING_PATH,
    FIT_SCORING_RECORDING_PATH,
    PASS_THRESHOLD,
    ROUTING_PASS_THRESHOLD,
    ROUTING_RECORDING_PATH,
    ManualCaptureClient,
    RecordingClient,
    ReplayClient,
    composition_gate,
    format_eval_summary,
    load_recording,
    routing_gate,
    run_composition_evals,
    run_fit_scoring_evals,
    run_jd_extraction_evals,
    run_routing_evals,
    save_recording,
)
from ..extraction import EXTRACTION_EVAL_FEATURE, extract_jd
from ..llm_client import (
    STUB_CLIENTS,
    default_client,
    default_composition_client,
    default_routing_client,
    default_scoring_client,
)
from ..mode import DEFAULT_DATA_DIR
from ..routing import ROUTING_EVAL_FEATURE, route_followup
from ..scoring import SCORING_EVAL_FEATURE, score_fit
from ._app import cli
from ._common import (
    EXIT_UNEXPECTED,
    EXIT_USAGE,
    _open_or_exit,
    _resolve_mode_or_exit,
    echo,
)


@cli.group("eval")
def eval_group() -> None:
    """Run an eval suite."""


def _recordable(inner: Any) -> Any:
    """Refuse to record a stub client: without a key there is nothing real to capture,
    and recording would overwrite the hand-captured responses with placeholder text."""
    if isinstance(inner, STUB_CLIENTS):
        echo(
            "no ANTHROPIC_API_KEY is set, so --record would capture the stub's placeholder "
            "over the recorded responses. Set a key, or use --manual to paste real output.",
            err=True,
        )
        sys.exit(EXIT_USAGE)
    return inner


@eval_group.command("jd_extraction")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs. Eval runs are metered to the llm_calls ledger.",
)
@click.option(
    "--record",
    "record",
    is_flag=True,
    default=False,
    help="Capture each live response to evals/jd_extraction/recorded.json.",
)
@click.option(
    "--replay",
    "replay",
    is_flag=True,
    default=False,
    help="Serve recorded responses instead of calling the model. No key, no spend.",
)
@click.option(
    "--min-pass-rate",
    type=float,
    default=PASS_THRESHOLD,
    show_default=True,
    help="Fail below this pass rate.",
)
def eval_jd_extraction(data_dir: Path, record: bool, replay: bool, min_pass_rate: float) -> None:
    """Run the JD-extraction eval suite (36 cases) against the current `extract_jd`.

    Exits non-zero when the pass *rate* falls below `--min-pass-rate`, which
    defaults to `PASS_THRESHOLD`. CI replays the committed recording and checks
    the published result still holds, which catches a grader or prompt change;
    it cannot judge the prompt against new input without a live model.

    Calls are recorded to the `llm_calls` ledger under the `extraction_eval`
    feature (D5), separate from production `extraction` traffic so prompt
    iteration shows up in `jscc costs` without inflating per-application
    cost.
    """
    if record and replay:
        raise click.UsageError("--record and --replay are mutually exclusive")

    client = None
    if replay:
        recorded = load_recording()
        if not recorded:
            echo("no recordings yet; run once with --record against a live key", err=True)
            sys.exit(EXIT_USAGE)
        client = ReplayClient(recorded, suite="jd_extraction")
    elif record:
        # Persist each capture to disk the moment it
        # happens, not only after the whole run returns -- a run that raises
        # partway through (a safety refusal, a transient API error, Ctrl-C)
        # used to discard every capture already paid for. save_recording
        # merges rather than overwrites, so writing one entry at a time
        # accumulates correctly instead of each write erasing the last.
        client = RecordingClient(
            _recordable(default_client()),
            on_captured=lambda key, text: save_recording({key: text}),
        )

    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        summary = run_jd_extraction_evals(
            lambda raw: extract_jd(raw, conn=conn, client=client, feature=EXTRACTION_EVAL_FEATURE)
        )
    finally:
        conn.close()

    if record and client is not None:
        # Every entry was already merged to disk by on_captured as the run
        # went; this is a final, redundant (and harmless -- save_recording
        # merges) flush plus the count for the message below.
        save_recording(client.captured)
        echo(f"recorded {len(client.captured)} responses")

    echo(format_eval_summary(summary))
    if summary.pass_rate < min_pass_rate:
        echo(
            f"pass rate {summary.pass_rate:.0%} is below the {min_pass_rate:.0%} bar",
            err=True,
        )
        sys.exit(1)


@eval_group.command("fit_scoring")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs. Eval runs are metered to the llm_calls ledger.",
)
@click.option(
    "--record",
    "record",
    is_flag=True,
    default=False,
    help="Capture each live response to evals/fit_scoring/recorded.json.",
)
@click.option(
    "--replay",
    "replay",
    is_flag=True,
    default=False,
    help="Serve recorded responses instead of calling the model. No key, no spend.",
)
@click.option(
    "--manual",
    "manual",
    is_flag=True,
    default=False,
    help=(
        "Capture via a human pasting each prompt into Claude.ai chat instead "
        "of a live API call (no ANTHROPIC_API_KEY needed). Implies --record."
    ),
)
@click.option(
    "--min-pass-rate",
    type=float,
    default=PASS_THRESHOLD,
    show_default=True,
    help="Fail below this pass rate.",
)
def eval_fit_scoring(
    data_dir: Path, record: bool, replay: bool, manual: bool, min_pass_rate: float
) -> None:
    """Run the fit-scoring eval suite (28 cases) against the current `score_fit`.

    Same shape as `eval jd_extraction` (C2a mirrors B2a): --record/--replay
    exist so C2b can validate the prompt against real model output. No
    ANTHROPIC_API_KEY is configured for this project, so `--manual` is how
    that actually happens -- one case at a time, this command prints the
    exact prompt to paste into Claude.ai chat and waits for the pasted-back
    response, persisting it immediately the same way a live `--record`
    run would.

    Calls are recorded to the `llm_calls` ledger under the `scoring_eval`
    feature (D5), separate from production `scoring` traffic.
    """
    if replay and (record or manual):
        raise click.UsageError("--replay is mutually exclusive with --record/--manual")
    record = record or manual

    client = None
    if replay:
        recorded = load_recording(FIT_SCORING_RECORDING_PATH)
        if not recorded:
            echo("no recordings yet; run once with --record against a live key", err=True)
            sys.exit(EXIT_USAGE)
        client = ReplayClient(recorded, suite="fit_scoring")
    elif record:
        inner = _recordable(ManualCaptureClient() if manual else default_scoring_client())
        client = RecordingClient(
            inner,
            on_captured=lambda key, text: save_recording({key: text}, FIT_SCORING_RECORDING_PATH),
        )

    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        summary = run_fit_scoring_evals(
            lambda extracted, raw_jd_text, profile: score_fit(
                extracted,
                raw_jd_text,
                profile,
                conn=conn,
                client=client,
                feature=SCORING_EVAL_FEATURE,
            )
        )
    finally:
        conn.close()

    if record and client is not None:
        save_recording(client.captured, FIT_SCORING_RECORDING_PATH)
        echo(f"recorded {len(client.captured)} responses")

    echo(format_eval_summary(summary))
    if summary.pass_rate < min_pass_rate:
        echo(
            f"pass rate {summary.pass_rate:.0%} is below the {min_pass_rate:.0%} bar",
            err=True,
        )
        sys.exit(1)


@eval_group.command("routing")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs. Eval runs are metered to the llm_calls ledger.",
)
@click.option(
    "--record",
    "record",
    is_flag=True,
    default=False,
    help="Capture each live response to evals/routing/recorded.json.",
)
@click.option(
    "--replay",
    "replay",
    is_flag=True,
    default=False,
    help="Serve recorded responses instead of calling the model. No key, no spend.",
)
@click.option(
    "--manual",
    "manual",
    is_flag=True,
    default=False,
    help=(
        "Capture via a human pasting each prompt into Claude.ai chat instead "
        "of a live API call (no ANTHROPIC_API_KEY needed). Implies --record."
    ),
)
@click.option(
    "--min-pass-rate",
    type=float,
    default=ROUTING_PASS_THRESHOLD,
    show_default=True,
    help="Fail below this combined pass rate.",
)
def eval_routing(
    data_dir: Path, record: bool, replay: bool, manual: bool, min_pass_rate: float
) -> None:
    """Run the routing eval suite (26 cases) against the current `route_followup`.

    Same shape as `eval fit_scoring`: --record/--replay exist to validate the
    prompt against real model output; --manual is how that happens, since no
    ANTHROPIC_API_KEY is configured for this project.

    Two separate gates, per D10: the combined pass rate must clear
    `--min-pass-rate` (85% by default, stricter than the 80%
    `jd_extraction`/`fit_scoring` use), AND every case where a genuinely
    non_routine situation got classified routine -- the false-routine
    failure D10 calls out as categorically worse than the rest -- must be
    zero, regardless of the combined rate. A prompt could clear 85% overall
    while still auto-drafting something it shouldn't; this command refuses
    to call that passing.

    Calls are recorded to the `llm_calls` ledger under the `routing_eval`
    feature (D5), separate from production `routing` traffic.
    """
    if replay and (record or manual):
        raise click.UsageError("--replay is mutually exclusive with --record/--manual")
    record = record or manual

    client = None
    if replay:
        recorded = load_recording(ROUTING_RECORDING_PATH)
        if not recorded:
            echo("no recordings yet; run once with --record against a live key", err=True)
            sys.exit(EXIT_USAGE)
        client = ReplayClient(recorded, suite="routing")
    elif record:
        inner = _recordable(ManualCaptureClient() if manual else default_routing_client())
        client = RecordingClient(
            inner,
            on_captured=lambda key, text: save_recording({key: text}, ROUTING_RECORDING_PATH),
        )

    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        summary = run_routing_evals(
            lambda app, history: route_followup(
                app, history, conn=conn, client=client, feature=ROUTING_EVAL_FEATURE
            )
        )
    finally:
        conn.close()

    if record and client is not None:
        save_recording(client.captured, ROUTING_RECORDING_PATH)
        echo(f"recorded {len(client.captured)} responses")

    echo(format_eval_summary(summary))

    failures = routing_gate(summary, min_pass_rate)
    for failure in failures:
        echo(failure, err=True)
    if failures:
        sys.exit(EXIT_UNEXPECTED)


@eval_group.command("composition")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs. Eval runs are metered to the llm_calls ledger.",
)
@click.option(
    "--record",
    "record",
    is_flag=True,
    default=False,
    help="Capture each live response to evals/composition/recorded.json.",
)
@click.option(
    "--replay",
    "replay",
    is_flag=True,
    default=False,
    help="Serve recorded responses instead of calling the model. No key, no spend.",
)
@click.option(
    "--manual",
    "manual",
    is_flag=True,
    default=False,
    help=(
        "Capture via a human pasting each prompt into Claude.ai chat instead "
        "of a live API call (no ANTHROPIC_API_KEY needed). Implies --record."
    ),
)
@click.option(
    "--min-pass-rate",
    type=float,
    default=COMPOSITION_PASS_THRESHOLD,
    show_default=True,
    help="Fail below this pass rate.",
)
def eval_composition(
    data_dir: Path, record: bool, replay: bool, manual: bool, min_pass_rate: float
) -> None:
    """Run the composition eval suite (28 cases: 25 draft, 3 escalate) against the current `compose_followup`.

    Same shape as `eval routing`: --record/--replay exist to validate the
    prompt against real model output; --manual is how that happens, since no
    ANTHROPIC_API_KEY is configured for this project. Two gates: the pass rate
    must clear 75%, and no case that required the composer to ask may come
    back as a draft.

    Calls are recorded to the `llm_calls` ledger under the `composition_eval`
    feature (D5), separate from production `composition` traffic.
    """
    if replay and (record or manual):
        raise click.UsageError("--replay is mutually exclusive with --record/--manual")
    record = record or manual

    client = None
    if replay:
        recorded = load_recording(COMPOSITION_RECORDING_PATH)
        if not recorded:
            echo("no recordings yet; run once with --record against a live key", err=True)
            sys.exit(EXIT_USAGE)
        client = ReplayClient(recorded, suite="composition")
    elif record:
        inner = _recordable(ManualCaptureClient() if manual else default_composition_client())
        client = RecordingClient(
            inner,
            on_captured=lambda key, text: save_recording({key: text}, COMPOSITION_RECORDING_PATH),
        )

    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        summary = run_composition_evals(
            lambda app, history, intent, style_samples: compose_followup(
                app,
                history,
                intent,
                style_samples,
                conn=conn,
                client=client,
                feature=COMPOSITION_EVAL_FEATURE,
            )
        )
    finally:
        conn.close()

    if record and client is not None:
        save_recording(client.captured, COMPOSITION_RECORDING_PATH)
        echo(f"recorded {len(client.captured)} responses")

    echo(format_eval_summary(summary))
    failures = composition_gate(summary, min_pass_rate)
    for failure in failures:
        echo(failure, err=True)
    if failures:
        sys.exit(EXIT_UNEXPECTED)
