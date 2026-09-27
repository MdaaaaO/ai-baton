#!/usr/bin/env python3
"""check_links.py — every relative Markdown link in the kit's tracked *.md files must resolve.

Links checked: `[text](path)` and `[text](path#anchor)` where `path` is relative (no scheme, no leading `/`, not
`mailto:`). Resolved against the linking file's directory, then against the kit root (README-style `docs/x.md` links
written from a subdirectory). Skipped: a `<placeholder>`, a bare word such as `url`, anything under `.context/` (the machine's
content, never in the kit), bare anchors (`#section`), links inside fenced code blocks, and the seeded templates
(`*.template.md`, `environment-template/`, `docs/templates/`), whose links are written for their seeded location. Anchors themselves are not verified (headings
move; the file is what a stale link breaks).

Usage: check_links.py [--repo DIR] [PATHS...]   (default: every tracked *.md); exit 1 with one line per broken link,
`path:line: broken link → target`. Stdlib only; uses `git ls-files` for the file list.
"""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def tracked_markdown(repo: Path) -> list[Path]:
    """Every tracked *.md except the templates setup.sh seeds elsewhere (`*.template.md`, `environment-template/`,
    `docs/templates/`): their links are written for the seeded location, not for the kit tree."""
    r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "*.md", "**/*.md"], capture_output=True, text=True)
    if r.returncode != 0:  # not a checkout, or git broken: an empty list here would be a green run over nothing
        raise SystemExit(f"check-links: cannot list the tracked Markdown files in {repo} (git ls-files exit {r.returncode}: "
                         f"{r.stderr.strip() or 'no message'}) — pass the files to check as arguments")
    files = {repo / f for f in r.stdout.split("\0") if f}
    return sorted(f for f in files if f.is_file() and not f.name.endswith(".template.md")
                  and "environment-template" not in f.parts and "templates" not in f.parts)


def skip(target: str) -> bool:
    path = target.split("#", 1)[0]
    return (target.startswith(("http://", "https://", "mailto:", "#", "/")) or "://" in target or "<" in target
            or target.startswith(".context/") or "/.context/" in target
            or ("/" not in path and "." not in path))  # a bare word such as `url` is a placeholder, not a path


def broken_links(path: Path, repo: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    in_fence = False
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in LINK.finditer(line):
            target = m.group(1).split("#", 1)[0]
            if not target or skip(m.group(1)):
                continue
            if not ((path.parent / target).exists() or (repo / target).exists()):
                out.append((n, m.group(1)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="check that relative Markdown links in the kit resolve")
    ap.add_argument("--repo", type=Path, default=KIT)
    ap.add_argument("paths", nargs="*", help="files to check (default: every tracked *.md)")
    a = ap.parse_args(argv)
    files = [Path(p) if Path(p).is_absolute() else a.repo / p for p in a.paths] or tracked_markdown(a.repo)
    if not files:
        print(f"check-links: FAIL — no Markdown files to check in {a.repo} (an empty list is not a pass)", file=sys.stderr)
        return 2
    bad = 0
    for f in files:
        for n, target in broken_links(f, a.repo):
            bad += 1
            rel = f.relative_to(a.repo) if f.is_relative_to(a.repo) else f
            print(f"{rel}:{n}: broken link → {target}")
    print(f"check-links: {'FAIL' if bad else 'OK'} — {len(files)} file(s), {bad} broken link(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
