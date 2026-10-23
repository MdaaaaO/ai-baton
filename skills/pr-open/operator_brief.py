#!/usr/bin/env python3
"""operator_brief.py — the mechanical half of pr-open's `## For the operator` block (docs/operator-brief.md):
`check` warns when the block is missing, when one of its four lines is missing, empty, repeated or longer
than 20 words, and `extract` prints the block's four lines for `ticket-close` and `session-handoff` to copy.
Stdlib only; reads the body from a file, from stdin, or from a PR.

Usage:
  operator_brief.py check   <body-file | -> | --pr <owner/repo> <n>
  operator_brief.py extract <body-file | -> | --pr <owner/repo> <n>

The four lines, in this order, each `- <Label>: <text>` (the bullet is optional):
  Can now: · Do differently: · Check with: · Not covered:
A line holds at most 20 words after its label (a `code span` counts as one word). When nothing applies, the
line says `Nothing.` and may add a short reason; it is never empty, `n/a` or left out.

`check` never blocks: warnings go to stderr, one per line, and the exit code is 0 unless the body could not be
read (2). `extract` prints the four lines as `- <Label>: <text>`; exit 1 when the body has no such section
(a PR that predates the rule), 0 otherwise.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "context-db" / "bin"))
import fsutil  # noqa: E402 — the one `<file | ->` reader
import verified  # noqa: E402 — gh_env / sh, the one place a PR is read

HEADING = "## For the operator"
LABELS = ("Can now", "Do differently", "Check with", "Not covered")
MAX_WORDS = 20
LABEL_RE = re.compile(r"^(?:[-*]\s+)?(" + "|".join(LABELS) + r"):\s*(.*)$", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"^(?:n/?a|none|tbd|todo|-+|\.+)\.?$", re.IGNORECASE)
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
FOOTER_RE = re.compile(r"^session `")  # the body's mandatory last line (pr-open SKILL.md step 1)
FENCE_RE = re.compile(r"^`{3,}")


def section(body: str) -> tuple[int, list[tuple[int, str]]] | None:
    """(the heading's 1-based line, [(line no, text)] for every non-blank line under it) — None when the body
    has no `## For the operator` heading. The section ends at the next `## ` heading, the footer line, an HTML
    comment line (a diagram-plan marker can follow the footer) or end of text."""
    lines = body.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == HEADING), None)
    if start is None:
        return None
    out: list[tuple[int, str]] = []
    in_fence = False
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if FENCE_RE.match(stripped):
            in_fence = not in_fence
        elif not in_fence and (lines[j].startswith("## ") or stripped.startswith("<!--") or FOOTER_RE.match(stripped)):
            break
        if stripped:
            out.append((j + 1, stripped))
    return start + 1, out


def words(text: str) -> int:
    """Whitespace-separated words, each `code span` counted as one."""
    return len(CODE_SPAN_RE.sub("X", text).split())


def parse(lines: list[tuple[int, str]]) -> tuple[dict[str, list[tuple[int, str]]], list[tuple[int, str]]]:
    """(label -> [(line no, text)] in file order, [(line no, line)] of every line that is none of the four)."""
    found: dict[str, list[tuple[int, str]]] = {}
    extra: list[tuple[int, str]] = []
    for no, line in lines:
        m = LABEL_RE.match(line)
        if m is None:
            extra.append((no, line))
            continue
        label = next(lab for lab in LABELS if lab.lower() == m.group(1).lower())
        found.setdefault(label, []).append((no, m.group(2).strip()))
    return found, extra


def warnings(body: str) -> list[str]:
    sec = section(body)
    if sec is None:
        return [f"MISSING — no `{HEADING}` section (docs/operator-brief.md)"]
    heading_no, lines = sec
    found, extra = parse(lines)
    out: list[str] = []
    for label in LABELS:
        hits = found.get(label, [])
        if not hits:
            out.append(f"{heading_no}: MISSING LINE `{label}:`")
            continue
        for no, text in hits[1:]:
            out.append(f"{no}: DUPLICATE `{label}:`")
        no, text = hits[0]
        if not text or PLACEHOLDER_RE.match(text):
            out.append(f"{no}: EMPTY `{label}:` — write `Nothing.` when nothing applies")
        elif words(text) > MAX_WORDS:
            out.append(f"{no}: LONG `{label}:` ({words(text)} words, max {MAX_WORDS})")
    for no, line in extra:
        shown = line if len(line) <= 40 else line[:39] + "…"
        out.append(f"{no}: EXTRA line, not one of the four ({shown})")
    return out


def read_body(a: argparse.Namespace) -> str:
    if a.pr:
        repo, n = a.pr[0], int(a.pr[1])
        return verified.sh(["gh", "api", f"repos/{repo}/pulls/{n}", "--jq", ".body // \"\""], verified.gh_env())
    if not a.file:
        raise OSError("give a body file, `-`, or --pr <owner/repo> <n>")
    return fsutil.read_arg(a.file)


def cmd_check(body: str) -> int:
    warns = warnings(body)
    for w in warns:
        print(f"operator: warn {w}", file=sys.stderr)
    if not warns:
        print("operator: ok")
    return 0


def cmd_extract(body: str) -> int:
    sec = section(body)
    if sec is None:
        print(f"operator: no `{HEADING}` section in this body", file=sys.stderr)
        return 1
    found, _ = parse(sec[1])
    for label in LABELS:
        if label in found:
            print(f"- {label}: {found[label][0][1]}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    for name, helptext in (("check", "warn on a missing block, a missing or empty line, or a line over 20 words"),
                           ("extract", "print the block's four lines")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("file", nargs="?", help="the PR body file, or - for stdin")
        p.add_argument("--pr", nargs=2, metavar=("OWNER/REPO", "N"), help="read the body of this PR instead of a file")
    a = ap.parse_args(argv[1:])
    try:
        body = read_body(a)
    except (OSError, SystemExit) as e:
        print(f"operator: {e}", file=sys.stderr)
        return 2
    return cmd_check(body) if a.mode == "check" else cmd_extract(body)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
