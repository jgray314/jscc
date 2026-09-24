"""Public files stay free of references to material that is not in the repo.

The Phase D gate scrubbed links into a private workspace and third-person
references to the author from the docs. The Phase E gate found both back within
days, because the scrub was a one-time edit and nothing checked it. This turns
the rule into a failing test: a public file may not point at a private
directory, cite a plan document the repo does not contain, or talk about its
author in the third person.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    ".capture",
    "data",
    ".safety",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
SUFFIXES = {".md", ".py", ".html", ".yml", ".yaml", ".toml", ".sh", ".txt"}

# Built from parts so this file does not itself contain the strings it forbids.
_AUTHOR = "Je" + "ss"
FORBIDDEN = {
    "a private workspace path": re.compile(r"context-" + r"directory"),
    "a reference to a plan the repo does not contain": re.compile(
        r"(?i)\b(parent[- ]plan|JSCC plan|sub-plan)\b"
    ),
    "the author in the third person": re.compile(rf"\b{_AUTHOR}(?:'s)?\b(?! Gray)"),
}


def _public_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP_DIRS or rel == Path(__file__).relative_to(ROOT):
            continue
        yield rel, path


def test_public_files_do_not_reference_private_material() -> None:
    offenders = []
    for rel, path in _public_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for what, pattern in FORBIDDEN.items():
                if pattern.search(line):
                    offenders.append(f"{rel}:{lineno}: {what}")
    assert not offenders, "public files reference private material:\n" + "\n".join(offenders)


def test_the_scan_sees_the_files_it_is_meant_to_guard() -> None:
    names = {rel.as_posix() for rel, _ in _public_files()}
    assert {"README.md", "CHANGELOG.md", "evals/README.md", "docs/threat-model.md"} <= names
