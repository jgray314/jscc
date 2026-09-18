"""Follow-up composition -- D10 step 2A of the routing-first drafter architecture.

`compose_followup` is the interface the eval suite (Slice D3) is written
against. Per D10, this is a Sonnet call reached only when `route_followup`
(D2) has already classified a situation `routine` -- a `non_routine`
situation never reaches this function; it gets a briefing card (D5) instead.
Getting composition wrong (wrong tone, hallucinated facts, ignoring the
declared intent) is a real failure, but a strictly smaller one than D2's
false-routine failure mode: the drafter already refused to auto-draft
anything a human needed to handle.

The Phase D4 slice replaces this stub with a real `@instrumented("composition")`
Sonnet call routed through `send_to_llm`; the signature stays
`(app, history, intent, style_samples) -> DraftEmail` so D3's eval cases
don't change shape when the prompt lands.
"""

from __future__ import annotations

from .models import Application, DraftEmail, Interaction


class CompositionNotImplementedError(NotImplementedError):
    """Raised by the Slice D3 stub -- no composition prompt exists yet."""


def compose_followup(
    app: Application,
    history: list[Interaction],
    intent: str,
    style_samples: list[str],
) -> DraftEmail:
    raise CompositionNotImplementedError(
        "compose_followup has no prompt yet (lands in Slice D4). The eval suite "
        "(`python -m jscc eval composition`) is expected to fail every case until "
        "then -- that failure is the harness working correctly, not a bug."
    )
