"""Command output goes through one control-character filter."""

from __future__ import annotations

import ast
from pathlib import Path

from jscc.terminal import printable, printable_field

CLI = Path(__file__).resolve().parents[1] / "jscc" / "cli"


def test_printable_keeps_layout_and_drops_controls() -> None:
    assert printable("a\n\tb\x1b[31mc\x9bd\x7f") == "a\n\tb[31mcd"


def test_printable_field_drops_newlines_too() -> None:
    assert printable_field("a\nb\x1b") == "ab"


def test_no_command_module_prints_with_click_echo_directly() -> None:
    offenders = []
    for path in sorted(CLI.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and path.name == "_common.py" and fn.name == "echo":
                continue
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "echo"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "click"
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"print through _common.echo, which strips control bytes: {offenders}"
