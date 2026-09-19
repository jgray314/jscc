"""Follow-up orchestration -- D10 step 2B and the top-level `followup()`.

`followup` is the one entry point for "what do I do about this application
next": route first (D2), then either compose a draft (D4, routine only) or
render a briefing card (non-routine). The router's bias (a false-routine
auto-draft is the larger failure) is what makes this the honest shape --
a non-routine situation never reaches `compose_followup`.

The renderer is deterministic on purpose: everything a briefing says was
already decided by the router (`reason`, `considerations`) or is a field of
the application. Adding a second LLM call to prettify a card the router just
wrote would spend money to re-derive nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from .composition import compose_followup
from .models import (
    Application,
    Briefing,
    DraftEmail,
    Interaction,
    RoutingClassification,
    RoutingDecision,
)
from .routing import route_followup

Router = Callable[..., RoutingDecision]
Composer = Callable[[Application, list[Interaction], str, list[str]], DraftEmail]


def render_briefing(app: Application, decision: RoutingDecision) -> Briefing:
    """Turn a `non_routine` decision into a briefing card.

    Raises `ValueError` on a routine decision: a routine situation has no
    reason or considerations to render, and quietly producing an empty card
    would hide a caller that skipped the composition branch.
    """
    if decision.classification != RoutingClassification.non_routine:
        raise ValueError("render_briefing requires a non_routine decision")
    # RoutingDecision doesn't enforce the prompt's "non_routine carries a
    # reason" rule, so a malformed-but-parsed decision lands here. The card
    # says so rather than printing "None".
    reason = decision.reason or "the router gave no reason -- treat as needing your judgment"
    return Briefing(
        application_id=app.id,
        company=app.company,
        title=app.title,
        stage=app.stage,
        source_url=app.source_url,
        reason=reason,
        considerations=list(decision.considerations),
    )


def format_briefing(briefing: Briefing) -> str:
    """Human-readable card text for the terminal."""
    lines = [
        f"HANDLE MANUALLY -- {briefing.company}: {briefing.title}",
        f"Stage: {briefing.stage}",
        f"Why: {briefing.reason}",
    ]
    if briefing.considerations:
        lines.append("Weigh before replying:")
        lines.extend(f"  - {item}" for item in briefing.considerations)
    lines.append(f"Application: {briefing.application_id}")
    if briefing.source_url:
        lines.append(f"Posting: {briefing.source_url}")
    return "\n".join(lines)


def followup(
    app: Application,
    history: list[Interaction],
    style_samples: list[str],
    *,
    conn: sqlite3.Connection | None = None,
    router: Router = route_followup,
    composer: Composer = compose_followup,
) -> DraftEmail | Briefing:
    """Route, then draft (routine) or brief (non-routine).

    `router`/`composer` are injectable so the orchestration is testable
    without either LLM step; production callers use the defaults. `conn` is
    forwarded to the router so the call lands in the `routing` ledger feature.
    """
    decision = router(app, history, conn=conn)
    if decision.classification == RoutingClassification.routine:
        # A routine decision without an intent can't be composed against;
        # refuse rather than guess -- the router's bias is toward a human.
        if not decision.intent:
            return render_briefing(
                app,
                RoutingDecision(
                    classification=RoutingClassification.non_routine,
                    reason="the router marked this routine but named no intent to draft against",
                    considerations=["Decide what the message should say; no draft was attempted"],
                ),
            )
        return composer(app, history, decision.intent, style_samples)
    return render_briefing(app, decision)
