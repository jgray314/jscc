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

The patterns themselves live in `jscc/personal_data.py`, not here. That module
is the single definition shared with the D7 M5 prompt sanitizer, so git egress
(M3) and LLM egress (M5) cannot drift apart on what counts as personal data —
they did drift before the B5 hardening slice, and the sanitizer lost.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

# Keep this script runnable as a bare `python scripts/precommit_scan.py ...`
# with no install step — `scripts/scan_tracked.sh` invokes it that way, from
# both CI and the pre-commit hook. Same bootstrap `scripts/smoke_fetch.py` uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jscc.personal_data import (  # noqa: E402
    default_danger_terms,
    find_personal,
    load_danger_list,
)


def _compile_exclude(pattern: str) -> re.Pattern[str]:
    """Compile a glob-style exclude to a full-match regex.

    Supports:
      - ``foo/**`` — matches ``foo`` and everything under ``foo/``.
        (``**`` after a ``/`` matches zero or more segments.)
      - ``**``     — any run of characters including ``/`` (recursive).
      - ``*``      — any run of characters within one path segment (no ``/``).
      - ``?``      — one non-``/`` character.
      - literal ``.`` and other regex metachars are escaped.

    `fnmatch` was rejected because it treats ``**`` as literal, giving
    ``tests/**`` a silent hole where anything under ``tests/`` still got
    scanned. That was M-exclude-1 in the A9 review. M-precommit-abs-paths-1
    in the A10 review added the zero-match ``/`` handling so ``tests/**``
    also covers the ``tests`` directory itself.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "/" and pattern[i + 1 : i + 3] == "**":
            # `foo/**` — match `foo` (no slash) OR `foo/anything`.
            out.append("(?:/.*)?")
            i += 3
        elif c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out))


def _to_repo_relative_posix(p: Path, cwd: Path) -> str:
    """Return `p` as a POSIX slash-joined path relative to `cwd` when possible.

    Windows contributors invoking the scanner with absolute paths (or
    pre-commit's file-list resolution passing absolute paths) previously
    silently defeated `--exclude`: `PurePosixPath(*Path('C:/.../CHANGELOG.md').parts)`
    yields `C:\\/Users/.../CHANGELOG.md`, and no natural glob `fullmatch`ed
    that string. M-precommit-abs-paths-1 in the A10 review.

    Falls back to the raw POSIX join for paths that live outside the tree —
    an intentional out-of-tree scan run should not silently drop excludes.
    """
    try:
        rel = p.resolve().relative_to(cwd.resolve())
    except (ValueError, OSError):
        return PurePosixPath(*p.parts).as_posix()
    return PurePosixPath(*rel.parts).as_posix()

class Hit:
    __slots__ = ("path", "line_no", "reason", "match")

    def __init__(self, path: Path, line_no: int, reason: str, match: str) -> None:
        self.path = path
        self.line_no = line_no
        self.reason = reason
        self.match = match

    def format(self) -> str:
        return f"{self.path}:{self.line_no}: {self.reason} -- {self.match!r}"


def scan_line(line: str, danger_terms: Iterable[str]) -> list[tuple[str, str]]:
    """Return (reason, matched-substring) tuples for every rule that fires.

    Thin alias over the shared definition so existing callers and tests keep
    working; the rules themselves live in `jscc/personal_data.py`.
    """
    return find_personal(line, danger_terms)


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
        default=None,
        help=(
            "Path to a newline-delimited danger substring list. Omit to use the "
            "package's .safety directory (both the tracked list and the local one)."
        ),
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

    # Both lists, via the same loader the sanitizer uses.
    #
    # Rerun-gate follow-on to H-1: this defaulted to the *tracked* scaffold
    # only, so `.safety/danger-list.local.txt` -- the file a user actually
    # edits when they realize something needs blocking -- was honoured by the
    # M5 sanitizer and ignored by the M3 scanner. A term added there blocked
    # LLM egress but not commits, which is the opposite of what the C1 fix
    # claims ("one edit blocks both"). Same drift as H-1, one file over.
    danger_terms = (
        load_danger_list(args.danger_list)
        if args.danger_list is not None
        else default_danger_terms()
    )
    excludes: list[re.Pattern[str]] = [_compile_exclude(p) for p in args.exclude]

    cwd = Path.cwd()

    def _excluded(p: Path) -> bool:
        posix = _to_repo_relative_posix(p, cwd)
        return any(pat.fullmatch(posix) for pat in excludes)

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
