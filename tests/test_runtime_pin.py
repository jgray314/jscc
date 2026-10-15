"""The Python the project runs on locally is the Python CI installs.

Found by the harness audit (2026-10-15): CI installed 3.12 while the working
machine ran 3.14, with `requires-python >= 3.12` the only statement. A green CI
run then said nothing about the version a developer actually ran, and the
reverse. One pin file, read by uv, and a test that CI names the same version.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pinned() -> str:
    return (ROOT / ".python-version").read_text(encoding="utf-8").strip()


def test_a_python_version_file_pins_major_and_minor() -> None:
    assert re.fullmatch(r"3\.\d+", pinned()), pinned()


def test_ci_installs_the_pinned_version() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = re.findall(r"uv python install (\S+)", ci)
    assert versions, "ci.yml no longer installs Python through uv"
    assert set(versions) == {pinned()}


def test_the_pin_satisfies_the_declared_minimum() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    floor = re.search(r">=\s*(3\.\d+)", project["requires-python"])
    assert floor, project["requires-python"]
    as_tuple = lambda v: tuple(int(p) for p in v.split("."))  # noqa: E731
    assert as_tuple(pinned()) >= as_tuple(floor.group(1))
