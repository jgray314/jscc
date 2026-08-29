"""Pre-commit content scanner for JSCC (D7 M3).

Scans staged files for personal-data-shaped strings that should never enter the
public repo. Exits 0 on clean, 1 on any hit. Reports every hit as
`<file>:<line>: <reason> -- <matched substring>` on stderr, so `pre-commit`
surfaces them all at once instead of one-at-a-time.

Standalone by design: `python scripts/precommit_scan.py path1 path2 ...` works
without the pre-commit framework installed, which is what the test suite drives.

Rules:
- Email regex: matches typical local@domain.tld.
- Phone regex: matches +?<digit><8-14 digits/dashes/spaces><digit> — deliberately
  loose; false positives are safer than misses.
- Danger list: substrings from `.safety/danger-list.txt` (one per line, comments
  with `#`, case-insensitive). Path override via `--danger-list`.
- Exclude: `--exclude GLOB` (repeatable, fnmatch on POSIX repo-relative paths)
  skips files that legitimately hold placeholder personal-data shapes — the
  scanner's own tests, this docstring, changelog entries describing it.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

EMAIL_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.IGNORECASE)
# Phone char class allows separator variants seen in the wild:
#   dashes / whitespace ((415) 555-0134 style)
#   parens (US area-code grouping)
#   dots (international dotted format, +44.20.7946.0018)
# Real disambiguation from noise happens in the digit-count filter below.
PHONE_RE = re.compile(r"\+?\d[\d\-\s().]{7,14}\d")

# Phone matches must contain a plausible number of digits. Real phone numbers
# have 10-15 digits (E.164). This is what disqualifies ISO dates like
# "2026-08-28" (8 digits) from the phone rule.
PHONE_DIGITS_MIN = 10
PHONE_DIGITS_MAX = 15

DEFAULT_DANGER_LIST = Path(".safety/danger-list.txt")


class Hit:
    __slots__ = ("path", "line_no", "reason", "match")

    def __init__(self, path: Path, line_no: int, reason: str, match: str) -> None:
        self.path = path
        self.line_no = line_no
        self.reason = reason
        self.match = match

    def format(self) -> str:
        return f"{self.path}:{self.line_no}: {self.reason} -- {self.match!r}"


def load_danger_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    terms: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line.lower())
    return terms


def scan_line(line: str, danger_terms: Iterable[str]) -> list[tuple[str, str]]:
    """Return (reason, matched-substring) tuples for every rule that fires."""
    hits: list[tuple[str, str]] = []
    for m in EMAIL_RE.finditer(line):
        hits.append(("email-pattern", m.group(0)))
    for m in PHONE_RE.finditer(line):
        digit_count = sum(1 for c in m.group(0) if c.isdigit())
        if PHONE_DIGITS_MIN <= digit_count <= PHONE_DIGITS_MAX:
            hits.append(("phone-pattern", m.group(0)))
    lower = line.lower()
    for term in danger_terms:
        idx = lower.find(term)
        if idx != -1:
            hits.append(("danger-list", line[idx : idx + len(term)]))
    return hits


def scan_file(path: Path, danger_terms: Iterable[str]) -> list[Hit]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        # Binary or unreadable — skip, do not fail. Binary blobs are the
        # domain of git-lfs / .gitattributes filters, not this scanner.
        return []
    out: list[Hit] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for reason, match in scan_line(line, danger_terms):
            out.append(Hit(path, i, reason, match))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JSCC pre-commit content scanner")
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument(
        "--danger-list",
        type=Path,
        default=DEFAULT_DANGER_LIST,
        help="Path to newline-delimited danger substring list.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help=(
            "Glob patterns (repo-relative, POSIX slashes) to skip. "
            "Use for files that legitimately contain placeholder personal-data "
            "shapes: the scanner's own tests, its docstring, changelog entries "
            "describing it. Repeatable."
        ),
    )
    args = parser.parse_args(argv)

    danger_terms = load_danger_list(args.danger_list)
    excludes: list[str] = args.exclude

    def _excluded(p: Path) -> bool:
        posix = PurePosixPath(*p.parts).as_posix()
        return any(fnmatch.fnmatch(posix, pat) for pat in excludes)

    all_hits: list[Hit] = []
    for f in args.files:
        if not f.is_file():
            continue
        if _excluded(f):
            continue
        all_hits.extend(scan_file(f, danger_terms))

    if all_hits:
        for hit in all_hits:
            print(hit.format(), file=sys.stderr)
        print(
            f"\nBLOCKED: {len(all_hits)} potential personal-data hit(s). "
            f"See D7 (dual-use safety) in the project plan; if the match is a "
            f"false positive, refine the regex or scrub the string.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
