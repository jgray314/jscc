"""Single definition of "personal-data-shaped" for both D7 egress points.

D7 names two mitigations that must agree on what counts as personal data:

- **M3** — the pre-commit scanner (`scripts/precommit_scan.py`): blocks it from
  entering the git repo.
- **M5** — the prompt sanitizer (`jscc/sanitizer.py`): blocks it from leaving
  the process toward an LLM.

Two enforcement points with two copies of the rules is a leak waiting for
one of them to fall behind: a string the scanner refuses must not be a string
the sanitizer forwards.

This module is the one definition. The scanner imports the patterns to
*detect*; the sanitizer imports `redact` to *rewrite*. Adding a term to
`.safety/danger-list.local.txt` now blocks it from both git and LLM traffic
with one edit, which is the property D7 M3/M5 were supposed to have all along.

**What this guarantees, precisely** (stated narrowly on purpose — see D8):

- Email-shaped tokens are removed.
- Phone-shaped digit runs (10-15 digits, E.164 range) are removed.
- Anthropic API keys (`sk-ant-…`) are removed.
- Every literal on the danger list is removed, case-insensitively.
- Any name in a supplied `name_roles` mapping becomes its role token.

**A credential is not personal data**, and it is deliberately not filed as
though it were. It carries its own reason label (`credential-pattern`) and its
own token, so D8's claim — which is about personal *identity* — neither widens
nor blurs by its presence here. What the two share is the boundary: this module
is what the D7 egress points agree must not cross one, and a leaked key is the
other obvious member of that set. Filing it under "personal" to reuse the
plumbing would trade a precise safety claim for a few saved lines, which is the
trade D7 and D8 exist to refuse.

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
from collections.abc import Iterable, Mapping
from os import environ
from pathlib import Path

from .paths import PACKAGE_ROOT

# Deliberately over-broad: matches any non-whitespace token containing `@` with a
# dot-separated tail. Covers ASCII, IDN local/domain parts, Punycode TLDs
# (`.xn--p1ai`), and non-ASCII TLDs. False positives are the design point of
# D7 — both egress points err toward blocking.
#
# The TLD class excludes trailing sentence punctuation (`,;:!?'"` and closing
# brackets) as well as `.`: none of it is TLD-valid, and without the exclusion
# "reach me at dana@x.example," redacts the comma along with the address --
# cosmetic, not a safety gap (the address itself never survives either way),
# but a gate finding (L-10) worth closing since the fix is one character class.
EMAIL_RE = re.compile(r"[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>().,;:!?'\"\]\)}]{2,}")

# Phone char class allows separator variants seen in the wild: dashes,
# whitespace, parens (US area-code grouping), dots (international dotted
# format). Real disambiguation from noise happens in the digit-count filter.
#
# Same false-positive class as a model id or a uv.lock hash (gate finding
# L-18): a long enough requisition-ID digit run in a real fetched JD --
# "Req ID 2026-04-118823" -- redacts as a phone number. Not fixed, on
# purpose: D7's design point is to err toward blocking, and this repo has
# no fetched-JD fixtures today to make it visible. It will be visible in a
# live run's redacted `source_raw`.
PHONE_RE = re.compile(r"\+?\d[\d\-\s().]{7,14}\d")

# Anthropic API keys. Narrow on purpose: the `sk-ant-` prefix plus a long
# key body is distinctive enough that a false positive is close to impossible,
# and a pattern that tried to catch "any high-entropy string" would fire on
# hashes, UUIDs and base64 blobs until someone turned it off. A rule people
# switch off protects nothing.
#
# This does not cover other vendors' key formats. That is a real limit rather
# than an oversight: this repo talks to one API, and a list of half-remembered
# prefixes for services it does not use would read as broader coverage than it
# has -- the same overstatement D8 is careful to avoid about names.
CREDENTIAL_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")

# Phone matches must contain a plausible number of digits. Real phone numbers
# have 10-15 digits (E.164). This is what disqualifies ISO dates from the
# phone rule.
PHONE_DIGITS_MIN = 10
PHONE_DIGITS_MAX = 15

# Anchored to the installed package, never to the process's working directory.
# A relative path here loads no terms at all outside the repo root -- silently,
# since the email and phone rules keep firing. The scanner always runs from the
# root and the sanitizer runs wherever the user is, so a cwd-relative list is a
# way for the two D7 egress points to disagree about what counts as personal.
# A control that depends on remembering to `cd` first is disciplinary; D7's
# claim is that it is structural.
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
CREDENTIAL_TOKEN = "[redacted-credential]"
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
    for m in CREDENTIAL_RE.finditer(line):
        hits.append(("credential-pattern", m.group(0)))
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
    """Rewrite every span this module blocks to a stable token.

    The rewrite half, used by the sanitizer (D7 M5). Order matters, for the same
    reason twice: a rule that rewrites *part* of a longer match leaves a mangled
    string the owning rule no longer recognises.

    Credentials go first. A key body is alphanumeric with dashes, so the phone
    rule can match a digit run inside one and replace it with a phone token,
    leaving a key that is still most of a key and no longer matches
    `CREDENTIAL_RE`. Emails go second, ahead of phones, because a long numeric
    local-part is eaten the same way.

    Note the asymmetry with the scanner: this *redacts* a credential where the
    scanner *blocks* it. That is deliberate. Blocking is the half that matters
    for a key -- a committed key is the damage -- while the sanitizer's contract
    is to rewrite unconditionally and never refuse work it can make safe.

    Gate finding L-13: `name_roles` now runs before the danger-term pass, not
    after. A name that happens to contain a listed danger term (a surname
    like "Reyes" matching a danger-list entry `reyes`) used to hit the danger
    pass first and become `Dana [redacted]`, which no longer matches
    `name_roles`'s full-name key -- the name was still gone (safety was never
    the gap), but the `[contact:recruiter]` tag `name_roles` exists to
    produce was silently lost instead. Running name substitution first means
    a term inside an already-tagged name has nothing left to match.
    """
    if not text:
        return text
    out = CREDENTIAL_RE.sub(CREDENTIAL_TOKEN, text)
    out = EMAIL_RE.sub(EMAIL_TOKEN, out)
    out = _redact_phones(out)
    for name, role in (name_roles or {}).items():
        out = _replace_case_insensitive(out, name, f"[contact:{role}]")
    for term in danger_terms:
        out = _replace_case_insensitive(out, term, DANGER_TOKEN)
    return out
