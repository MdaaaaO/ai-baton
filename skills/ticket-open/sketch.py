#!/usr/bin/env python3
"""sketch.py — the ticket Sketch's marker: stamp it, parse it, check it at pickup (docs/diagrams.md
§ The sketch marker). `ticket-open` draws the Sketch (WHERE + WHAT target + WHY) by hand or SKIPs it;
this script owns only the marker that proves a drawing happened and lets a later session verify it
still matches the repo.

The marker is one HTML comment per repo a ticket touches:
  <!-- sketch: repo=<owner/repo> paths=<entry>[,<entry>…] components=<name>[,<name>…] hash=<12 hex> -->
A tracker that renders no HTML comments (Jira) shows that line as raw text, so there the marker run goes
alone inside one bare ``` fence (`stamp --tracker jira`); the marker line itself is unchanged and the fence
lines are not part of the hash. `check` and `parse` read both shapes, and a fence of 4+ backticks, `~~~` or
with an info string (```text) as well.
`paths`/`components` are comma-separated, sorted, deduplicated, or `-` for "none"; a path entry
ending in `/` is a directory, a leading `+` is a path the ticket creates (checked by its parent
directory instead). `hash` is the first 12 hex digits of a SHA-256 over the Sketch's own text (from
its heading — a `#`-heading line or a `**Sketch**` line — to the marker block, CRLF normalized,
each line right-stripped, and outside code fences in canonical markdown — table cells stripped with
`---` separators, bullets as `- <text>` with no indent, escaped brackets plain, blank lines dropped — so a
tracker connector's markdown rewrite (Jira's) keeps the hash; fenced lines verbatim; joined by `\n`).

Usage (stdlib only, no network):
  sketch.py stamp [FILE] --repo-slug <owner/repo> [--paths <entry>[,<entry>…]] [--components <name>[,<name>…]]
                  [--tracker github|jira]
      Reads the Sketch section from FILE, or stdin when FILE is omitted or `-`. Prints the same text
      to stdout with the `repo-slug`'s marker appended (no existing marker for that repo) or replaced
      (one already there) — the hash is always freshly computed from the text given, excluding any
      marker line and the fence around it. `--tracker` is the resolved `tracker.kind`: `jira` writes the
      marker run inside a ``` fence after a blank line, `github` (the default) writes it bare. Every
      marker in the output, other repos' included, takes the requested shape. Writes nothing to disk;
      the caller decides where the stamped text goes.
  sketch.py check <file-or-text> [--repo-dir <dir>] [--base origin/main]
      `file-or-text`: an existing file's path, or the literal text itself. Reads every marker in it
      (ticket body then comments, oldest first, as the caller concatenated them) and keeps the last
      one per repo. `--repo-dir` (default `.`) names the clone whose `origin` remote picks which
      repo's marker applies; `--base` is the default branch to check paths against
      (`git ls-tree -r --name-only <base>`, run in `--repo-dir`). `components=` is never checked here.
      Prints exactly one of: `SKETCH OK`, one `MOVED <path>` line per missing entry (sorted),
      `STALE SKETCH <old>→<new>` (the Sketch text changed since it was stamped — restamp it), or
      `MALFORMED SKETCH <line>` (a `<!-- sketch: …`-shaped line that does not parse). A ticket with no
      marker for the repo (including one that only has `Sketch: SKIP (<reason>)`) prints `NO SKETCH`.
  sketch.py parse <file-or-text>
      Prints the last marker found (by text position, across all repos) as one JSON object:
      {"repo": …, "paths": […], "components": […], "hash": "…"} (empty lists for `-`).

Exit codes: 0 `SKETCH OK` / `NO SKETCH` / a plan printed; 1 a `git` read failed; 2 bad usage (no
marker to parse, an invalid `--paths`/`--components` entry, a `--repo-dir` with no `owner/repo`
`origin` remote); 3 `MOVED`, `STALE SKETCH` or `MALFORMED SKETCH`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

MARKER_RE = re.compile(
    r"^\s*<!-- sketch: repo=(\S+) paths=(\S+) components=(\S+) hash=([0-9a-f]{12}) -->\s*$"
)
MARKER_PREFIX = "<!-- sketch:"
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})([^`]*)$")  # an opening or closing code fence line; group 2 = info string
TRACKERS = ("github", "jira")
HEADING_RE = re.compile(r"^\s*(#{1,6}\s+\S.*|\*\*Sketch\*\*)\s*$")
PATH_INVALID = re.compile(r"[ ,]")
COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# ── shared parsing / formatting ────────────────────────────────────────────────────────────────
def split_list(raw: str) -> list[str]:
    """`a,b,c` or `-` (empty) → a sorted, deduplicated list; whitespace around a comma is trimmed."""
    if raw.strip() in ("", "-"):
        return []
    return sorted({s.strip() for s in raw.split(",") if s.strip()})


def validate_paths(entries: list[str]) -> None:
    """Bad usage (exit 2), never a git/read failure — raised directly so `cmd_stamp` need not check."""
    for e in entries:
        body = e[1:] if e.startswith("+") else e
        if body.startswith("/"):
            print(f"FAIL --paths entry {e!r} has a leading /", file=sys.stderr)
            raise SystemExit(2)
        if ".." in body.split("/"):
            print(f"FAIL --paths entry {e!r} contains ..", file=sys.stderr)
            raise SystemExit(2)
        if PATH_INVALID.search(body):
            print(f"FAIL --paths entry {e!r} contains a space or an embedded comma", file=sys.stderr)
            raise SystemExit(2)


def validate_components(entries: list[str]) -> None:
    for c in entries:
        if not COMPONENT_RE.match(c):
            print(f"FAIL --components entry {c!r} is not [A-Za-z0-9._-]+", file=sys.stderr)
            raise SystemExit(2)


TABLE_SEP_CELL_RE = re.compile(r"^(:?)-+(:?)$")  # a separator-row cell: `---`, `:--`, `--:`, `:-:` (any dash count)
LIST_ITEM_RE = re.compile(r"^\s*[-*+]\s+(\S.*)$")  # a bullet line, any marker, any indent
BRACKET_ESCAPE_RE = re.compile(r"\\([\[\]])")  # `\[` / `\]` — a connector's escaping of plain brackets


def canonical_line(line: str) -> str:
    """One rstripped line outside a code fence in the form every markdown round-trip agrees on — what a tracker
    connector that rewrites markdown (Jira: `|---|` → `| --- |`, `| |` → `|  |`, `-` → `*`, 2 → 4 indent
    spaces, `[` → `\\[`) leaves unchanged, so the posted text and the stored text hash the same (#589).
    A table row keeps its cells, stripped, with a separator cell as `---` plus its colons; a bullet keeps its
    text behind one `- `, its indent dropped; escaped brackets are plain. Anything else is returned as is."""
    stripped = line.strip()
    if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
        cells = [c.strip() for c in stripped[1:-1].split("|")]
        cells = [f"{m.group(1)}---{m.group(2)}" if (m := TABLE_SEP_CELL_RE.match(c)) else c for c in cells]
        line = "|" + "|".join(cells) + "|"
    elif (m := LIST_ITEM_RE.match(line)):
        line = "- " + m.group(1)
    return BRACKET_ESCAPE_RE.sub(r"\1", line)


def normalize_lines(lines: list[str]) -> list[str]:
    """The hash's own normalization, shared by `stamp` and `check`: CRLF → LF (the caller already split on
    `\\n`, so this right-strips each line), then — outside code fences only — `canonical_line` on every
    line and blank lines dropped (a connector inserts whitespace-only lines between nested items). Lines
    inside a fence (the ASCII WHERE box, the marker's own fence) are kept verbatim: a connector returns them
    byte-identical, and `|` or `-` there is drawing, not a table or a list."""
    out: list[str] = []
    fence: tuple[str, int] | None = None  # (fence char, length) of the fence currently open
    for raw in lines:
        ln = raw.rstrip()
        m = FENCE_RE.match(ln)
        if m:
            run, info = m.groups()
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not info.strip():
                fence = None
            out.append(ln)
            continue
        if fence is not None:
            out.append(ln)
            continue
        ln = canonical_line(ln)
        if ln == "":
            continue
        out.append(ln)
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return out


def content_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def find_heading(lines: list[str], before: int) -> int | None:
    """The index of the last heading line (a `#`-heading or `**Sketch**`) at or before `before - 1`,
    or None when the text carries no heading — the hash then covers everything up to `before`."""
    for i in range(before - 1, -1, -1):
        if HEADING_RE.match(lines[i]):
            return i
    return None


def fence_open_at(lines: list[str], upto: int) -> tuple[str, int, int] | None:
    """The code fence still open after `lines[:upto]` as (fence char, length, opening line index), else None.
    A fence closes on a line of the same char, at least as long, with no info string — the Markdown rule, so
    a longer fence (4+ backticks), a `~~~` fence and an info string (```text) all count."""
    state: tuple[str, int, int] | None = None
    for i in range(upto):
        m = FENCE_RE.match(lines[i])
        if not m:
            continue
        run, info = m.groups()
        if state is None:
            state = (run[0], len(run), i)
        elif run[0] == state[0] and len(run) >= state[1] and not info.strip():
            state = None
    return state


def marker_boundary(lines: list[str]) -> int:
    """The index where the hashed slice ends: the first marker line (any repo) — the start of the trailing
    run `stamp` writes them as — or `len(lines)` when none are present yet. When that first marker sits
    alone in a code fence (the Jira shape: the fence opens on the line directly above it and closes on the
    line after the marker run) the boundary is the opening fence line instead, so the fence is never
    hashed. A fence line above the marker that closes the drawing's own block (an even number of fence
    lines precede it) is not an opener and stays in the hashed text. The slice always ends here, for both
    `stamp` and `check`, so a marker's hash never depends on which other repos' markers sit beside it, in
    what order, or whether the run is fenced."""
    marker_idxs = [i for i, ln in enumerate(lines) if MARKER_RE.match(ln)]
    if not marker_idxs:
        return len(lines)
    first = marker_idxs[0]
    state = fence_open_at(lines, first)
    if state is not None and state[2] == first - 1:
        j = first
        while j < len(lines) and MARKER_RE.match(lines[j]):
            j += 1
        m = FENCE_RE.match(lines[j]) if j < len(lines) else None
        if m and m.group(1)[0] == state[0] and len(m.group(1)) >= state[1] and not m.group(2).strip():
            return first - 1
    return first


def malformed_line(text: str) -> str | None:
    """The first `<!-- sketch:`-shaped line present that `MARKER_RE` cannot parse — a hand edit that
    dropped a field or the closing `-->`. Distinguishes that from no marker at all."""
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if MARKER_RE.match(line):
            continue
        if line.lstrip().startswith(MARKER_PREFIX):
            return line.strip()
    return None


def parse_markers(text: str) -> dict[str, dict]:
    """Every valid marker in `text`, keyed by repo — later occurrences overwrite earlier ones, so a
    forward scan alone gives "last marker per repo wins"."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: dict[str, dict] = {}
    for i, line in enumerate(lines):
        m = MARKER_RE.match(line)
        if not m:
            continue
        repo, paths_raw, comps_raw, h = m.groups()
        out[repo] = {"idx": i, "paths": split_list(paths_raw), "components": split_list(comps_raw), "hash": h}
    return out


def read_file_or_text(arg: str) -> str:
    """`check`/`parse`'s `file-or-text`: an existing file's content, else the argument itself."""
    p = Path(arg)
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    except OSError:
        pass
    return arg


# ── stamp ────────────────────────────────────────────────────────────────────────────────────
def stamp_text(raw: str, repo_slug: str, paths: list[str], components: list[str], tracker: str = "github") -> str:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    marker_idxs = [i for i, ln in enumerate(lines) if MARKER_RE.match(ln)]
    content_end = marker_boundary(lines)
    heading_idx = find_heading(lines, content_end)
    start = heading_idx + 1 if heading_idx is not None else 0
    h = content_hash(normalize_lines(lines[start:content_end]))
    new_marker = (f"<!-- sketch: repo={repo_slug} paths={','.join(paths) or '-'} "
                  f"components={','.join(components) or '-'} hash={h} -->")
    kept = [lines[i] for i in marker_idxs if MARKER_RE.match(lines[i]).group(1) != repo_slug]
    content = lines[:content_end]
    while content and content[-1].strip() == "":
        content.pop()
    markers = kept + [new_marker]
    if tracker == "jira":  # that tracker's renderer has no HTML-comment node: the run goes in a code fence
        return "\n".join(content + ["", "```"] + markers + ["```"]) + "\n"
    return "\n".join(content + markers) + "\n"


def cmd_stamp(a: argparse.Namespace) -> int:
    # validate before touching stdin: a bad --paths/--components must fail fast, never block on input
    paths = split_list(a.paths)
    components = split_list(a.components)
    validate_paths(paths)
    validate_components(components)
    raw = sys.stdin.read() if a.file in (None, "-") else Path(a.file).read_text(encoding="utf-8")
    sys.stdout.write(stamp_text(raw, a.repo_slug, paths, components, a.tracker))
    return 0


# ── check ────────────────────────────────────────────────────────────────────────────────────
def repo_slug_of(repo_dir: str) -> str:
    try:
        p = subprocess.run(["git", "-C", repo_dir, "remote", "get-url", "origin"],
                            capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"FAIL git -C {repo_dir} remote get-url origin timed out after 60s")
    if p.returncode != 0:
        raise SystemExit(f"FAIL git -C {repo_dir} remote get-url origin: {p.stderr.strip()[:300]}")
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", p.stdout.strip())
    if not m:
        print(f"FAIL --repo-dir {repo_dir}: origin remote {p.stdout.strip()!r} is not an owner/repo URL",
              file=sys.stderr)
        raise SystemExit(2)
    return m.group(1)


def default_branch_tree(repo_dir: str, base: str) -> set[str]:
    try:
        p = subprocess.run(["git", "-C", repo_dir, "ls-tree", "-r", "--name-only", base],
                            capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"FAIL git -C {repo_dir} ls-tree -r --name-only {base} timed out after 60s")
    if p.returncode != 0:
        raise SystemExit(f"FAIL git -C {repo_dir} ls-tree -r --name-only {base}: {p.stderr.strip()[:300]}")
    return set(p.stdout.splitlines())


def path_covered(entry: str, tree: set[str]) -> bool:
    if entry.startswith("+"):
        parent = entry[1:].rsplit("/", 1)[0] if "/" in entry[1:] else ""
        if parent == "":
            return True  # the repo root always exists
        prefix = parent + "/"
        return any(t.startswith(prefix) for t in tree)
    if entry.endswith("/"):
        return any(t.startswith(entry) for t in tree)
    return entry in tree


def cmd_check(a: argparse.Namespace) -> int:
    text = read_file_or_text(a.text)
    bad = malformed_line(text)
    if bad is not None:
        print(f"MALFORMED SKETCH {bad}")
        return 3
    markers = parse_markers(text)
    if not markers:
        print("NO SKETCH")
        return 0
    repo = repo_slug_of(a.repo_dir)
    entry = markers.get(repo)
    if entry is None:
        print("NO SKETCH")
        return 0
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_end = marker_boundary(lines)
    heading_idx = find_heading(lines, content_end)
    start = heading_idx + 1 if heading_idx is not None else 0
    fresh_hash = content_hash(normalize_lines(lines[start:content_end]))
    if fresh_hash != entry["hash"]:
        print(f"STALE SKETCH {entry['hash']}→{fresh_hash}")
        return 3
    tree = default_branch_tree(a.repo_dir, a.base)
    moved = sorted(p for p in entry["paths"] if not path_covered(p, tree))
    if moved:
        for p in moved:
            print(f"MOVED {p}")
        return 3
    print("SKETCH OK")
    return 0


# ── parse ────────────────────────────────────────────────────────────────────────────────────
def cmd_parse(a: argparse.Namespace) -> int:
    text = read_file_or_text(a.text)
    markers = parse_markers(text)
    if not markers:
        bad = malformed_line(text)
        print(f"FAIL {'malformed sketch marker: ' + bad if bad else 'no sketch marker found'}", file=sys.stderr)
        return 2
    repo = max(markers, key=lambda r: markers[r]["idx"])
    entry = markers[repo]
    print(json.dumps({"repo": repo, "paths": entry["paths"], "components": entry["components"], "hash": entry["hash"]}))
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_stamp = sub.add_parser("stamp", help="stamp the marker into a Sketch section")
    p_stamp.add_argument("file", nargs="?", default=None, help="the Sketch text's file, or omit/`-` for stdin")
    p_stamp.add_argument("--repo-slug", required=True, metavar="OWNER/REPO")
    p_stamp.add_argument("--paths", default="-", metavar="ENTRY[,ENTRY…]")
    p_stamp.add_argument("--components", default="-", metavar="NAME[,NAME…]")
    p_stamp.add_argument("--tracker", default="github", choices=TRACKERS, metavar="KIND",
                         help="the resolved tracker.kind: jira fences the marker, github (default) leaves it bare")

    p_check = sub.add_parser("check", help="check a ticket's sketch marker against the default branch")
    p_check.add_argument("text", metavar="FILE-OR-TEXT")
    p_check.add_argument("--repo-dir", default=".", metavar="DIR")
    p_check.add_argument("--base", default="origin/main", metavar="REF")

    p_parse = sub.add_parser("parse", help="print the last marker's fields as JSON")
    p_parse.add_argument("text", metavar="FILE-OR-TEXT")

    a = ap.parse_args(argv[1:])
    if a.cmd == "stamp":
        return cmd_stamp(a)
    if a.cmd == "check":
        return cmd_check(a)
    return cmd_parse(a)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
