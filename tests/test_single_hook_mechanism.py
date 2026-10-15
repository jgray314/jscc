"""One git-hook mechanism: `.githooks`, enabled with core.hooksPath.

History: ADR-004 chose the pre-commit framework, the repo later moved to
versioned `.githooks`, and the framework config and its install instruction
stayed behind. The framework is not installed anywhere, so a reader following
the docs ran a command that failed and a config that nothing used.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# History is allowed to describe the old mechanism; instructions are not.
HISTORY = {
    "CHANGELOG.md",
    "decisions/004-precommit-framework.md",
    "PROGRESS.md",  # generated: quotes the feature that states this rule
}


def tracked(pattern: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", pattern], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout
    return out.split()


def test_the_pre_commit_framework_config_is_gone() -> None:
    assert not (ROOT / ".pre-commit-config.yaml").exists()
    assert ".pre-commit-config.yaml" not in tracked(".pre-commit-config.yaml")


def test_the_versioned_hooks_exist() -> None:
    for hook in ("commit-msg", "pre-push"):
        assert (ROOT / ".githooks" / hook).is_file(), hook


def test_no_current_doc_tells_a_reader_to_install_the_framework() -> None:
    offenders = []
    for rel in tracked("*.md") + tracked("*.sh") + tracked("*.yml"):
        if rel in HISTORY:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in ("pre-commit install", ".pre-commit-config.yaml"):
            if needle in text:
                offenders.append(f"{rel}: mentions '{needle}'")
    assert not offenders, "\n".join(offenders)


def test_adr_004_says_it_is_superseded() -> None:
    adr = (ROOT / "decisions" / "004-precommit-framework.md").read_text(encoding="utf-8")
    status_line = next(line for line in adr.splitlines() if line.startswith("**Status:**"))
    assert "Superseded" in status_line
