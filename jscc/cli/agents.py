"""Per-application LLM commands: score, route, followup."""

from __future__ import annotations

import sys
from pathlib import Path

import anthropic
import click
from pydantic import ValidationError

from ..composition import CompositionParseError
from ..config import (
    LoadError,
    load_profile,
    resolve_profile_path,
)
from ..followup import followup, format_briefing
from ..llm_client import (
    UnknownModelPricingError,
)
from ..mode import DEFAULT_DATA_DIR
from ..models import (
    Briefing,
    ExtractedJD,
    RoutingClassification,
)
from ..routing import ROUTING_FEATURE, RoutingParseError, route_followup
from ..scoring import SCORING_FEATURE, ScoringParseError, score_fit
from ..storage import (
    get_application,
    list_interactions,
    update_application,
)
from ._app import cli
from ._common import (
    DEFAULT_CONFIG_DIR,
    EXIT_UNEXPECTED,
    EXIT_USAGE,
    _open_or_exit,
    _resolve_mode_or_exit,
    echo,
)


@cli.command("score")
@click.argument("application_id")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory containing profile.*.yaml.",
)
def score(application_id: str, data_dir: Path, config_dir: Path) -> None:
    """Score an existing application's fit against the active profile.

    Requires the application to already have `extracted_jd` populated (from
    `ingest`) -- there's nothing to score against otherwise. Per D9, the
    scorer sees both that structured extraction and the application's raw
    JD text, plus the profile loaded via `resolve_profile_path` (private if
    present, else the public example).
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        app = get_application(conn, application_id)
        if app is None:
            echo(f"no application found with id {application_id!r}", err=True)
            sys.exit(EXIT_USAGE)
        if app.extracted_jd is None:
            echo(
                f"application {application_id} has no extracted_jd yet -- run "
                "`ingest`/`resolve-dlq` first.",
                err=True,
            )
            sys.exit(EXIT_USAGE)

        try:
            profile_path = resolve_profile_path(config_dir, mode=mode)
            profile = load_profile(profile_path)
        except (LoadError, ValidationError) as e:
            echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)

        extracted = ExtractedJD(**app.extracted_jd)
        try:
            result = score_fit(
                extracted, app.source_raw, profile, conn=conn, feature=SCORING_FEATURE
            )
        except ScoringParseError as e:
            echo(f"scoring failed: {e}", err=True)
            sys.exit(EXIT_UNEXPECTED)
        except anthropic.APIError as e:
            # Same reasoning as `ingest`'s H-6 handling: a transient API
            # error should not crash with a raw traceback. Scoring has no
            # DLQ concept (nothing was fetched to re-queue) so this is a
            # plain failure, not a queued one -- retry is just `score` again.
            echo(f"LLM API error: {e}", err=True)
            sys.exit(EXIT_UNEXPECTED)
        except UnknownModelPricingError as e:
            echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)

        update_application(
            conn, application_id, fit_score=result.score, fit_rationale=result.rationale
        )
        echo(f"scored application {application_id}: {result.score} — {result.rationale}")
    finally:
        conn.close()


@cli.command("route")
@click.argument("application_id")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
def route(application_id: str, data_dir: Path) -> None:
    """Classify whether the next follow-up for an application is routine.

    Per D10, this is step 1 of the routing-first drafter -- a classification
    only, not a draft. No `Application` field stores the decision (nothing
    in the data model needs it, and the sub-plan's D2 DoD doesn't call for
    persistence); this command exists so the routing decision is inspectable
    on its own before composition (step 2A) and the non-routine briefing
    renderer (step 2B) land in later Phase D slices.
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        app = get_application(conn, application_id)
        if app is None:
            echo(f"no application found with id {application_id!r}", err=True)
            sys.exit(EXIT_USAGE)
        history = list_interactions(conn, application_id)

        try:
            decision = route_followup(app, history, conn=conn, feature=ROUTING_FEATURE)
        except RoutingParseError as e:
            echo(f"routing failed: {e}", err=True)
            sys.exit(EXIT_UNEXPECTED)
        except anthropic.APIError as e:
            # Same reasoning as `score`'s handling of the same exception.
            echo(f"LLM API error: {e}", err=True)
            sys.exit(EXIT_UNEXPECTED)
        except UnknownModelPricingError as e:
            echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)

        if decision.classification == RoutingClassification.routine:
            echo(f"routine (intent={decision.intent})")
        else:
            echo(f"non_routine: {decision.reason}")
            for item in decision.considerations:
                echo(f"  - {item}")
    finally:
        conn.close()


@cli.command("followup")
@click.argument("application_id")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory containing profile.*.yaml.",
)
def followup_cmd(application_id: str, data_dir: Path, config_dir: Path) -> None:
    """Draft the next follow-up, or brief you on why it needs a human.

    Per D10: route first; a routine situation gets a drafted email (subject
    and body), a non-routine one gets a briefing card and no draft. Style
    samples come from the active profile, loaded the same way `score` does.
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        app = get_application(conn, application_id)
        if app is None:
            echo(f"no application found with id {application_id!r}", err=True)
            sys.exit(EXIT_USAGE)
        history = list_interactions(conn, application_id)

        try:
            profile = load_profile(resolve_profile_path(config_dir, mode=mode))
        except (LoadError, ValidationError) as e:
            echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)
        if not profile.style_samples:
            # Checked before routing, so no call is billed for a draft that could
            # not be written in the candidate's voice.
            echo("configuration error: the profile has no style_samples to draft from", err=True)
            sys.exit(EXIT_USAGE)

        try:
            result = followup(app, history, profile.style_samples, conn=conn)
        except RoutingParseError as e:
            echo(f"routing failed: {e}", err=True)
            sys.exit(EXIT_UNEXPECTED)
        except CompositionParseError as e:
            echo(f"drafting failed: {e}", err=True)
            sys.exit(EXIT_UNEXPECTED)
        except anthropic.APIError as e:
            echo(f"LLM API error: {e}", err=True)
            sys.exit(EXIT_UNEXPECTED)
        except UnknownModelPricingError as e:
            echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)

        if isinstance(result, Briefing):
            echo(format_briefing(result))
        else:
            echo(f"Subject: {result.subject}\n\n{result.body}")
    finally:
        conn.close()
