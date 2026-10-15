"""Email detection is linear in the input, and byte-identical to the pattern it replaced.

The email pattern tried every start position inside a long run of non-whitespace
text and rescanned to the end of the run each time, so a 160 KB single token (a
base64 blob, a minified file, a pasted URL list with no spaces) took 46 seconds in
the sanitizer and the scanner. The matcher now anchors on each `@` and extends
left to the start of its run, which finds the same matches in one pass.

Because redaction output feeds the recorded-eval prompt hash, equivalence is the
property that matters more than speed: the reference pattern below is the one the
code used before, and every generated input must redact identically under both.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from jscc.personal_data import EMAIL_RE, EMAIL_TOKEN, find_personal, redact

REFERENCE = re.compile(r"[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>().,;:!?'\"\]\)}]{2,}")
BUDGET_SECONDS = 1.0
SIZE = 200_000

HOSTILE_INPUTS = {
    "one long token": "x" * SIZE,
    "one long token ending in an at sign": "x" * SIZE + "@",
    "a long dotted token": "a." * (SIZE // 2),
    "an at sign then a long dotless run": "a@" + "x" * SIZE,
    "a long run of at signs": "a@" * (SIZE // 2),
    "a long run of at signs and dots": "a@b." * (SIZE // 4),
    "a long run of punctuation": ",;:!?" * (SIZE // 5),
    "a long run with a real address at the end": "x" * SIZE + " jane.doe" + "@" + "example.org",
}


@pytest.mark.parametrize("name", sorted(HOSTILE_INPUTS))
def test_redacting_a_long_input_stays_inside_the_time_budget(name: str) -> None:
    text = HOSTILE_INPUTS[name]
    started = time.perf_counter()
    redact(text)
    find_personal(text, [])
    elapsed = time.perf_counter() - started
    assert elapsed < BUDGET_SECONDS, f"{name}: {elapsed:.2f}s for {len(text)} chars"


def test_a_real_address_at_the_end_of_a_long_run_is_still_redacted() -> None:
    out = redact("x" * SIZE + " jane.doe" + "@" + "example.org")
    assert out.endswith(" " + EMAIL_TOKEN)


_ALPHABET = list("ab.@,;:!?'\"()<> \t]})-_9") + ["\n", "é", "x" + "@" + "y.zz", ".co", "a@b.c"]


def _random_text(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 60)))


def test_matches_and_redaction_are_identical_to_the_reference_pattern() -> None:
    rng = random.Random(20261015)
    for _ in range(20_000):
        text = _random_text(rng)
        expected = [m.span() for m in REFERENCE.finditer(text)]
        assert [m.span() for m in EMAIL_RE.finditer(text)] == expected, repr(text)
        assert EMAIL_RE.sub(EMAIL_TOKEN, text) == REFERENCE.sub(EMAIL_TOKEN, text), repr(text)


def test_the_continuation_after_a_match_matches_the_reference() -> None:
    """The case a start-of-run anchor gets wrong: the next address begins on a
    punctuation character the previous match stopped at."""
    at = "@"  # split so the content scanner does not read the fixture as an address
    text = f"a{at}b.cc,d{at}e.ff and g{at}h.ii.jj."
    assert [m.group(0) for m in EMAIL_RE.finditer(text)] == [
        m.group(0) for m in REFERENCE.finditer(text)
    ]
