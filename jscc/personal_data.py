"""Single definition of "personal-data-shaped" for both D7 egress points.

D7 names two mitigations that must agree on what counts as personal data:

- **M3** — the pre-commit scanner (`scripts/precommit_scan.py`): blocks it from
  entering the git repo.
- **M5** — the prompt sanitizer (`jscc/sanitizer.py`): blocks it from leaving
  the process toward an LLM.

Before the B5 hardening slice these were not the same definition — M3 had
real regexes and M5 had an identity `_transform` that redacted nothing. A
string carrying a name, email, and phone was refused by the scanner and
forwarded verbatim by the sanitizer. That was finding C1 of the Phase B gate.

This module is now the one definition. The scanner imports the patterns to
*detect*; the sanitizer imports `redact` to *rewrite*. Adding a term to
`.safety/danger-list.local.txt` now blocks it from both git and LLM traffic
with one edit, which is the property D7 M3/M5 were supposed to have all along.

**What this guarantees, precisely** (stated narrowly on purpose — see D8):

- Email-shaped tokens are removed.
- Phone-shaped digit runs (10-15 digits, E.164 range) are removed.
- Every literal on the danger list is removed, case-insensitively.
- Any name in a supplied `name_roles` mapping becomes its role token.

**What it does not guarantee:** arbitrary person names in free text are not
detected. That needs NER, not regex, and pretending a regex does it would be
worse than not claiming it — the claim is the dangerous part. Callers holding
known contact names (the `contacts` table, Phase D's drafter) pass them via
`name_roles` and get role-token substitution; unknown names in pasted prose
are out of scope and D8's wording reflects that.

This file is on the scanner's exclude list for the same reason
`scripts/precommit_scan.py` is: it contains the patterns themselves, which
self-match.
"""

from __future__ import annotations

import re
import warnings
from os import environ
from pathlib import Path
from typing import Iterable, Mapping

from .paths import PACKAGE_ROOT

# Deliberately over-broad: matches any non-whitespace token containing `@` with a
# dot-separated tail. Covers ASCII, IDN local/domain parts, Punycode TLDs
# (`.xn--p1ai`), and non-ASCII TLDs. False positives are the design point of
# D7 — both egress points err toward blocking.
EMAIL_RE = re.compile(r"[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>().]{2,}")

# Phone char class allows separator variants seen in the wild: dashes,
# whitespace, parens (US area-code grouping), dots (international dotted
# format). Real disambiguation from noise happens in the digit-count filter.
PHONE_RE = re.compile(r"\+?\d[\d\-\s().]{7,14}\d")

# Phone matches must contain a plausible number of digits. Real phone numbers
# have 10-15 digits (E.164). This is what disqualifies ISO dates from the
# phone rule.
PHONE_DIGITS_MIN = 10
PHONE_DIGITS_MAX = 15

# Anchored to the installed package, never to the process's working directory.
#
# Rerun-gate finding H-1: these were relative paths, so `default_danger_terms()`
# returned `[]` for any process not started from the repo root -- silently, with
# emails and phones still redacting so nothing looked broken. That is the
# load-bearing half of the C1 fix: the pre-commit scanner always runs from the
# repo root, the sanitizer runs wherever the user happens to be, so the two D7
# egress points drifted on what counts as personal after all -- through path
# resolution rather than through the duplicated regexes C1 removed. A control
# whose effectiveness depends on remembering to `cd` first is disciplinary, and
# D7's whole claim is that it is structural. `evals.py` already anchored this
# way; this file should have.
SAFETY_DIR_ENV_VAR = "JSCC_SAFETY_DIR"


def safety_dir() -> Path:
    """Directory holding the danger lists.

    `JSCC_SAFETY_DIR` overrides, for installed use where the package does not
    sit next to a checkout. Read per call rather than captured at import, for
    the same reason `default_danger_terms()` re-reads the files themselves.
    """
    override = environ.get(SAFETY_DIR_ENV_VAR)
    if override:
        path = Path(override).expanduser()
        if not path.is_dir():
            raise SafetyConfigError(
                f"{SAFETY_DIR_ENV_VAR}={override!r} is not a directory. Unset it to "
                f"use the default ({PACKAGE_ROOT / '.safety'}), or point it at a "
                "directory holding danger-list.txt / danger-list.local.txt."
            )
        return path
    return PACKAGE_ROOT / ".safety"


class SafetyConfigError(RuntimeError):
    """The danger-list location is configured but unusable.

    Raised rather than defaulted: an explicitly-set safety path that does not
    resolve means the operator believes a list is loaded when none is. Failing
    loudly is the whole point -- the silent-empty-list behaviour is the bug
    this replaces.
    """

EMAIL_TOKEN = "[redacted-email]"
PHONE_TOKEN = "[redacted-phone]"
DANGER_TOKEN = "[redacted]"


def load_danger_list(path: Path) -> list[str]:
    """Read one lowercased substring per line; `#` comments and blanks skipped."""
    if not path.exists():
        return []
    terms: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line.lower())
    return terms


def default_danger_terms() -> list[str]:
    """Committed scaffold list plus the gitignored local override.

    Loaded fresh rather than cached at import: the local list is the file a
    user edits when they realize something needs blocking, and a cached
    module-level copy would silently ignore the edit until restart.

    Warns if the directory is missing entirely. Both files being *empty* is
    a normal state (the tracked scaffold ships with no terms); the directory
    not existing means the lists are not where this process thinks they are,
    which is the condition that used to pass unnoticed.
    """
    directory = safety_dir()
    if not directory.is_dir():
        warnings.warn(
            f"no danger-list directory at {directory}; name-based redaction is "
            f"inactive (email and phone patterns still apply). Set "
            f"{SAFETY_DIR_ENV_VAR} to point at one.",
            RuntimeWarning,
            stacklevel=2,
        )
        return []
    return load_danger_list(directory / "danger-list.txt") + load_danger_list(
        directory / "danger-list.local.txt"
    )


def _phone_digit_count(match: str) -> int:
    return sum(1 for c in match if c.isdigit())


def find_personal(line: str, danger_terms: Iterable[str]) -> list[tuple[str, str]]:
    """Return (reason, matched-substring) for every rule that fires on `line`.

    The detection half, used by the pre-commit scanner (D7 M3).
    """
    hits: list[tuple[str, str]] = []
    for m in EMAIL_RE.finditer(line):
        hits.append(("email-pattern", m.group(0)))
    for m in PHONE_RE.finditer(line):
        if PHONE_DIGITS_MIN <= _phone_digit_count(m.group(0)) <= PHONE_DIGITS_MAX:
            hits.append(("phone-pattern", m.group(0)))
    lower = line.lower()
    for term in danger_terms:
        idx = lower.find(term)
        if idx != -1:
            hits.append(("danger-list", line[idx : idx + len(term)]))
    return hits


def _replace_case_insensitive(text: str, term: str, token: str) -> str:
    if not term:
        return text
    return re.sub(re.escape(term), token, text, flags=re.IGNORECASE)


def _redact_phones(text: str) -> str:
    def _sub(m: re.Match[str]) -> str:
        if PHONE_DIGITS_MIN <= _phone_digit_count(m.group(0)) <= PHONE_DIGITS_MAX:
            return PHONE_TOKEN
        return m.group(0)

    return PHONE_RE.sub(_sub, text)


def redact(
    text: str,
    *,
    danger_terms: Iterable[str] = (),
    name_roles: Mapping[str, str] | None = None,
) -> str:
    """Rewrite every personal-data-shaped span in `text` to a stable token.

    The rewrite half, used by the sanitizer (D7 M5). Order matters: emails go
    first, because a long numeric local-part would otherwise be eaten by the
    phone rule and leave a mangled address the email rule no longer matches.
    """
    if not text:
        return text
    out = EMAIL_RE.sub(EMAIL_TOKEN, text)
    out = _redact_phones(out)
    for term in danger_terms:
        out = _replace_case_insensitive(out, term, DANGER_TOKEN)
    for name, role in (name_roles or {}).items():
        out = _replace_case_insensitive(out, name, f"[contact:{role}]")
    return out
