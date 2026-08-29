"""JD extraction — D9 step 1 of the split extract/score architecture.

`extract_jd` is the interface the eval suite (Slice B1) is written against.
The Phase B2 slice replaces this stub with a real `@instrumented("extraction")`
Haiku call routed through `send_to_llm`; the signature stays `(raw_text) ->
ExtractedJD` so B1's eval cases don't change shape when the prompt lands.
"""
from __future__ import annotations

from .models import ExtractedJD


class ExtractionNotImplementedError(NotImplementedError):
    """Raised by the Slice B1 stub — no extraction prompt exists yet."""


def extract_jd(raw_text: str) -> ExtractedJD:
    raise ExtractionNotImplementedError(
        "extract_jd has no prompt yet (lands in Slice B2). The eval suite "
        "(`python -m jscc eval jd_extraction`) is expected to fail every "
        "case until then — that failure is the harness working correctly, "
        "not a bug."
    )
