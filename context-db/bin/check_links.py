#!/usr/bin/env python3
"""check_links.py — every relative Markdown link, and every `<file> § <heading>` pointer, in the kit's
tracked *.md files must resolve.

Links checked: `[text](path)` and `[text](path#anchor)` where `path` is relative (no scheme, no leading `/`, not
`mailto:`). Resolved against the linking file's directory, then against the kit root (README-style `docs/x.md` links
written from a subdirectory). Skipped: a `<placeholder>`, a bare word such as `url`, anything under `.context/` (the machine's
content, never in the kit), bare anchors (`#section`), links inside fenced code blocks, and the seeded templates
(`*.template.md`, `environment-template/`, `docs/templates/`), whose links are written for their seeded location. Anchors themselves are not verified (headings
move; the file is what a stale link breaks).

Section pointers checked: an inline `` `<file>.md` § <Heading> `` (or without backticks) resolves `<file>` the same
way a link does, then the cited words must be a case-insensitive prefix of one of the target's `#`/`##`/… headings
or a `**Bold**` run-in label opening a line (`WORKSPACE.md`'s `**Verification**`, `docs/layout.md`'s `**Content
root.**` conventions) — trailing words that do not narrow to a match are dropped one at a time, so "§ Skills asks
of the text" still finds "Skills — the contract, …".
A `<file>` that does not resolve to a tracked file (an ambiguous same-name reference such as bare `SKILL.md`, or a
`.context/`-rooted doc, never in the kit) is not checked at all — under-checking here is the safe direction. Bare
`CLAUDE.md` is exempt: skill bodies cite it for the session's *assembled* root file (`WORKSPACE.md`'s content plus
whatever the machine added), never the kit's own placeholder.

`docs/` may not tell a contributor to run the clone-only `make -C .claude/…` form: that only works on a clone
install. Say `make -C $BATON/context-db …` instead (`docs/packaging.md` § Kit root) so the command works on a
plugin install too.
Exempt: `docs/new-environment.md`, whose install prompt runs before `$BATON` exists (it is cloning `.claude/` for
the first time).

Usage: check_links.py [--repo DIR] [PATHS...]   (default: every tracked *.md); exit 1 with one line per broken link
or pointer, `path:line: broken link → target` / `path:line: dangling section pointer → target`. Stdlib only; uses
`git ls-files` for the file list.
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
SECTION_POINTER = re.compile(r"`?([\w./-]+\.md)`?\s*§\s*([A-Z][^`),.:\n]*)")
# The clone install's kit root, built from a variable rather than spelled out here: kit_verify's own
# HARDCODED_KIT_PATH rule (#3) flags this exact shape (the literal root directly followed by `context-db`)
# anywhere outside a comment, and this file is one of the tracked scripts that rule scans.
CLONE_KIT_ROOT = ".claude"
CLAUDE_MAKE = re.compile(rf"make(?:\s+-s)?\s+-C\s+{re.escape(CLONE_KIT_ROOT)}/context-db\b")
CLAUDE_MAKE_DOCS_EXEMPT = {"docs/new-environment.md"}


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


def headings(path: Path) -> list[str]:
    """Every ATX `#` heading and standalone `**Bold**` run-in label (WORKSPACE.md's `**Verification**`
    convention), text only — markdown emphasis markers stripped."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if not m:
            m = re.match(r"^\*\*([^*]+)\*\*", line.strip())
        if m:
            out.append(re.sub(r"[`*_]", "", m.group(1)).strip())
    return out


def section_exists(cited: str, target_headings: list[str]) -> bool:
    """`cited` names a heading in `target_headings` when it (or itself with trailing words dropped one at a
    time) is a case-insensitive prefix of one — "Skills" is a prefix of "Skills — the contract, …"."""
    words = cited.strip().split()
    lowered = [h.lower() for h in target_headings]
    while words:
        candidate = " ".join(words).lower().rstrip(".,:;")
        if candidate and any(h.startswith(candidate) for h in lowered):
            return True
        words.pop()
    return False


def broken_section_pointers(path: Path, repo: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    in_fence = False
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in SECTION_POINTER.finditer(line):
            file_ref, cited = m.group(1), m.group(2).strip()
            if file_ref == "CLAUDE.md":  # the session's assembled root file, never the kit's own placeholder
                continue
            target = None
            for cand in (path.parent / file_ref, repo / file_ref):
                if cand.is_file():
                    target = cand
                    break
            if target is None:  # an ambiguous same-name reference, or `.context/`: not ours to check
                continue
            if not section_exists(cited, headings(target)):
                out.append((n, f"{file_ref} § {cited}"))
    return out


def claude_prefixed_make_calls(path: Path) -> list[int]:
    """Line numbers citing the clone-only `make -C .claude/…` form: wrong outside a clone, so a docs/ example
    must say `make -C $BATON/context-db …` instead (works on both install paths)."""
    out: list[int] = []
    in_fence = False
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if CLAUDE_MAKE.search(line):
            out.append(n)
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
        rel = f.relative_to(a.repo) if f.is_relative_to(a.repo) else f
        for n, target in broken_links(f, a.repo):
            bad += 1
            print(f"{rel}:{n}: broken link → {target}")
        for n, target in broken_section_pointers(f, a.repo):
            bad += 1
            print(f"{rel}:{n}: dangling section pointer → {target}")
        if rel.as_posix().startswith("docs/") and rel.as_posix() not in CLAUDE_MAKE_DOCS_EXEMPT:
            for n in claude_prefixed_make_calls(f):
                bad += 1
                print(f"{rel}:{n}: cites the clone-only `make -C {CLONE_KIT_ROOT}/…` form — say "
                      "`make -C $BATON/context-db …` so the command works on a plugin install too")
    print(f"check-links: {'FAIL' if bad else 'OK'} — {len(files)} file(s), {bad} broken link(s)/pointer(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
