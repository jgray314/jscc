"""The dependency-install command is stated once and quoted identically.

Found by the harness audit (2026-10-15): AGENTS.md and CI said
`uv sync --all-extras --frozen` while the README and the technical reference
said plain `uv sync`. A reader following the README got no extras and an
unfrozen lockfile, and the first failure had nothing to do with their change.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_OF_TRUTH = "AGENTS.md"
QUOTED_IN = ["README.md", "docs/technical-reference.md", ".github/workflows/ci.yml"]
SYNC = re.compile(r"uv sync(?:[ \t]+--[\w-]+)*")


def commands(rel: str) -> list[str]:
    return SYNC.findall((ROOT / rel).read_text(encoding="utf-8"))


def test_agents_md_states_exactly_one_install_command() -> None:
    found = set(commands(SOURCE_OF_TRUTH))
    assert len(found) == 1, found


def test_every_quoted_install_command_matches_agents_md() -> None:
    (canonical,) = set(commands(SOURCE_OF_TRUTH))
    wrong = {rel: sorted(set(commands(rel)) - {canonical}) for rel in QUOTED_IN}
    wrong = {rel: cmds for rel, cmds in wrong.items() if cmds}
    assert not wrong, f"expected '{canonical}' everywhere, found {wrong}"


def test_each_quoting_file_actually_contains_the_command() -> None:
    """A file that dropped the command entirely would pass the test above."""
    for rel in QUOTED_IN:
        assert commands(rel), f"{rel} no longer states the install command"
