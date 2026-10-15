"""Every link in tracked Markdown points at something that exists.

Why this is its own test: the claims registry pins counts and status phrases,
but a README heading that was renamed left two dead anchors (one inside the
repo, one in the portfolio index that links to it) and nothing noticed.
"""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(```|~~~)")
OWN_REPO = re.compile(
    r"^https://github\.com/jgray314/jscc(?:/(?:blob|tree)/main/([^#]*))?(?:#(.*))?$"
)


def tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout
    return [ROOT / p for p in out.split() if (ROOT / p).exists()]


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: set[str] = set()
    seen: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m and not in_fence:
            base = slug(m.group(1))
            n = seen.get(base, 0)
            seen[base] = n + 1
            found.add(base if n == 0 else f"{base}-{n}")
    return found


def links_in(path: Path):
    in_fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in LINK.finditer(re.sub(r"`[^`]*`", "", line)):
            yield lineno, m.group(1)


def resolve(source: Path, target: str) -> tuple[Path | None, str | None, bool]:
    """Return (file, anchor, checkable). Unchecked: external URLs, mailto."""
    own = OWN_REPO.match(target)
    if own:
        rel, anchor = own.group(1), own.group(2)
        return (ROOT / rel if rel else ROOT / "README.md"), anchor, True
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
        return None, None, False
    file_part, _, anchor = target.partition("#")
    file = source if not file_part else (source.parent / unquote(file_part)).resolve()
    return file, anchor or None, True


def test_every_relative_link_and_anchor_resolves() -> None:
    problems = []
    cache: dict[Path, set[str]] = {}
    for md in tracked_markdown():
        for lineno, target in links_in(md):
            file, anchor, checkable = resolve(md, target)
            where = f"{md.relative_to(ROOT).as_posix()}:{lineno}"
            if not checkable:
                continue
            if file is None or not file.exists():
                problems.append(f"{where}: {target} -> missing file")
                continue
            if anchor and file.suffix == ".md":
                heads = cache.setdefault(file, anchors(file))
                if anchor.lower() not in heads:
                    problems.append(f"{where}: {target} -> no heading '{anchor}' in {file.name}")
    assert not problems, "dead links:\n" + "\n".join(problems)


def test_the_link_checker_catches_a_dead_anchor(tmp_path: Path) -> None:
    """The checker itself must be able to fail, or the test above guards nothing."""
    page = tmp_path / "page.md"
    page.write_text(
        "# Real heading\n\n[ok](#real-heading) [bad](#renamed-heading)\n", encoding="utf-8"
    )
    heads = anchors(page)
    assert "real-heading" in heads
    assert "renamed-heading" not in heads
    assert [t for _, t in links_in(page)] == ["#real-heading", "#renamed-heading"]
