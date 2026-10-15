"""The README's eval figures, tied to the published results.

Counts the docs state (tests, ADRs, threats, phases, headline figures) live in the
one registry in tests/test_docs_claims.py. What stays here is README-specific: the
eval figures must be the published ones and must carry their caveat, and a figure
that was once the headline may appear only beside what replaced it.
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_eval_figures_match_the_published_results() -> None:
    """Every published eval result appears in the README, and no suite with a
    recording is described as unvalidated.

    The README once said composition was "not yet validated" in three places and
    24/28 in two others. The figures themselves are pinned to the recordings by
    tests/test_published_results.py; this ties the README to that same table.
    """
    from tests.test_published_results import PUBLISHED

    text = README.read_text(encoding="utf-8")
    for suite, (passed, total) in PUBLISHED.items():
        if suite == "jd_extraction":
            # Cited by percentage, with its caveat: the figure must not stand alone.
            figure = f"{round(100 * passed / total)}%"
            assert figure in text, suite
            for match in re.finditer(re.escape(figure), text):
                nearby = text[max(0, match.start() - 400) : match.end() + 400]
                assert "held-out" in nearby, (
                    f"README cites {suite}'s {figure} without saying it is not held out"
                )
        else:
            assert f"{passed}/{total}" in text, f"README does not cite {suite}'s {passed}/{total}"
    assert "not yet validated" not in text


# A figure that was once the headline and has since been replaced. Each may still
# appear as history, but only beside the later result that superseded it.
SUPERSEDED = {
    "21/25": ("27/28", "64%"),  # fit_scoring's first prompt
    "27/33": ("32/36",),  # the 33-case extraction suite
}
_CONTEXT_CHARS = 300


def test_a_superseded_figure_appears_only_beside_what_replaced_it() -> None:
    """The README's opening paragraph kept saying fit scoring was 84% (21/25)
    after it had been recaptured at 64% and then 27/28. The existing check only
    asked that the current figures appear somewhere, so it stayed green."""
    text = README.read_text(encoding="utf-8")
    for old, replacements in SUPERSEDED.items():
        for match in re.finditer(re.escape(old), text):
            nearby = text[max(0, match.start() - _CONTEXT_CHARS) : match.end() + _CONTEXT_CHARS]
            assert any(r in nearby for r in replacements), (
                f"README cites the superseded {old} at offset {match.start()} without "
                f"any of {replacements} nearby"
            )
