"""Text that is safe to print to a terminal.

Application fields come from fetched postings and model output, and a model can
emit a JSON escape that decodes to a raw control byte. A terminal escape sequence
needs one of those bytes to do anything, so removing them removes the primitive
without parsing escape sequences. The C1 range is included because some terminals
treat a single 0x9b byte as the start of a control sequence.
"""

from __future__ import annotations

import re

_CONTROL_EXCEPT_LAYOUT = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ANY_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def printable(text: str) -> str:
    """Remove control characters, keeping newlines and tabs."""
    return _CONTROL_EXCEPT_LAYOUT.sub("", text)


def printable_field(text: str) -> str:
    """Remove every control character, newlines included, for a one-line field."""
    return _ANY_CONTROL.sub("", text)
