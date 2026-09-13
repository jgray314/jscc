"""Fit scoring — D9 step 2 of the split extract/score architecture.

`score_fit` is the interface the eval suite (Slice C1) is written against.
Per D9, the scorer sees both the extracted structured JD AND the raw JD text
-- extraction inevitably loses signal (nuanced language that doesn't fit
clean structured fields but matters for fit judgment), and the augmented
input is a locked decision, not a future refinement.

The Phase C2 slice replaces this stub with a real `@instrumented("scoring")`
Sonnet call routed through `send_to_llm`; the signature stays
`(extracted, raw_jd_text, profile) -> FitResult` so C1's eval cases don't
change shape when the prompt lands.
"""

from __future__ import annotations

from .config import Profile
from .models import ExtractedJD, FitResult


class FitScoringNotImplementedError(NotImplementedError):
    """Raised by the Slice C1 stub — no scoring prompt exists yet."""


def score_fit(extracted: ExtractedJD, raw_jd_text: str, profile: Profile) -> FitResult:
    raise FitScoringNotImplementedError(
        "score_fit has no prompt yet (lands in Slice C2). The eval suite "
        "(`python -m jscc eval fit_scoring`) is expected to fail every "
        "case until then — that failure is the harness working correctly, "
        "not a bug."
    )
