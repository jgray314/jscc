"""Follow-up routing -- D10 step 1 of the routing-first drafter architecture.

`route_followup` is the interface the eval suite (Slice D1) is written
against. Per D10, this is a Haiku classification call, not a draft: it
decides whether a situation is `routine` (safe for Step 2A to compose a
draft) or `non_routine` (Step 2B renders a briefing card instead -- no
prose, no auto-draft). Getting this wrong in the false-routine direction
(auto-drafting a situation that needed a human) is the larger product
failure per D10 -- Slice D2 biases the prompt accordingly, but that bias
has nothing to build against until this stub exists.

The Phase D2 slice replaces this stub with a real `@instrumented("routing")`
Haiku call routed through `send_to_llm`; the signature stays
`(app, history) -> RoutingDecision` so D1's eval cases don't change shape
when the prompt lands.
"""

from __future__ import annotations

from .models import Application, Interaction, RoutingDecision


class RoutingNotImplementedError(NotImplementedError):
    """Raised by the Slice D1 stub -- no routing prompt exists yet."""


def route_followup(app: Application, history: list[Interaction]) -> RoutingDecision:
    raise RoutingNotImplementedError(
        "route_followup has no prompt yet (lands in Slice D2). The eval suite "
        "(`python -m jscc eval routing`) is expected to fail every case until "
        "then -- that failure is the harness working correctly, not a bug."
    )
