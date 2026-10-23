#!/usr/bin/env python3
"""done_when.py — a ticket's `## Done when` block as stable, checkable criteria, and which of them a PR body covers.

`ticket-open` writes the block, `ticket-pickup` probes it, the worker brief quotes it, `pr-open` proves each
criterion or names the gap, `ticket-close` delivers per id. One grammar, one line per criterion, under a
`## Done when` heading:

  - [ ] DW-<n> <criterion> · check: <command | test | observation>

`DW-<n>` is stable for the ticket's life (a later criterion takes the next number, never a reused one). The
check is a command when it is exactly one backticked span (`` `make test` ``), else a test or an observation a
person confirms — only a command is runnable.

Usage:
  done_when.py parse <ticket-file | ->
      One line per criterion: `DW-<n>`, `command` or `manual`, the check (a command is printed without its
      backticks), the criterion — tab-separated. Reads every `## Done when` section in the text (pass the body,
      then the comments, oldest first, as `sizing.py` does).
  done_when.py coverage --ticket <ticket-file | -> --body <pr-body-file | ->
      Prints exactly `DONE WHEN: covered` or `DONE WHEN: gap DW-<n>[, DW-<n>…]`. An id is covered when
        - a line in the body's `## Verified` section reads `DW-<n>[, DW-<m>…] · <evidence>` and the evidence
          is one of evidence_check.py's shapes (a `cmd` → `output`, a URL, a #<n> or commit reference), or
        - a line in its `## One honest limit` section reads `DW-<n>[, DW-<m>…] · <why it is not verified>`
          (covered with a limit: stated, not proven — each such id is also named on stderr).
      Anything else, including a Verified line whose evidence is bare, leaves the id in the gap. An id the body
      cites that the ticket never defined is named on stderr and changes nothing.
  done_when.py probe --ticket <ticket-file | -> --repo-dir <dir> [--timeout <s>]
      Runs each command check in <dir> (shell, default 120 s timeout) and prints one line per id:
      `DW-<n> passing`, `DW-<n> failing (exit <code>)`, `DW-<n> timeout` or `DW-<n> manual`. It runs the
      ticket's text as a shell command: only on a ticket you trust.

Exit codes: 0 ok (`coverage`: covered); 1 the ticket has no `DW-<n>` criteria (a ticket that predates the
block); 2 bad usage, an unreadable file, or a malformed block (a checkbox line with no id or no `· check:`, a
repeated id); 3 (`coverage` only) a gap. Stdlib only.
"""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_check as ec  # noqa: E402 — the one place the evidence-shape rules live
import fsutil  # noqa: E402 — the one `<file | ->` reader

DONE_WHEN_HEAD = "## Done when"
VERIFIED_HEAD = "## Verified"
LIMIT_HEAD = "## One honest limit"
FOOTER_RE = re.compile(r"^session `")  # a PR body's mandatory last line ends the section above it
FENCE_RE = re.compile(r"^(`{3,})")
BOX_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]")
# `- [ ] DW-3 <criterion> · check: <check>` — the criterion may hold ` · ` itself; the LAST `· check:` splits.
CRITERION_RE = re.compile(
    r"^\s*[-*]\s*\[[ xX]\]\s+DW-(?P<n>\d+)\s+(?P<crit>\S.*?)\s+·\s+check:\s*(?P<check>\S.*?)\s*$"
)
COMMAND_RE = re.compile(r"^`(?P<cmd>[^`]+)`$")
# `[- ]DW-1[, DW-2…] · <tail>` — a covering line in `## Verified` / `## One honest limit`.
IDS_LINE_RE = re.compile(r"^(?:[-*]\s+)?(?P<ids>DW-\d+(?:\s*,\s*DW-\d+)*)\s+·\s+(?P<tail>\S.*)$")
ID_RE = re.compile(r"DW-\d+")
DEFAULT_PROBE_TIMEOUT = 120


class Criterion:
    __slots__ = ("id", "text", "check", "command")

    def __init__(self, n: int, text: str, check: str):
        self.id = f"DW-{n}"
        self.text = text
        self.check = check
        m = COMMAND_RE.match(check)
        self.command = m.group("cmd").strip() if m else None


# ── sections ─────────────────────────────────────────────────────────────────────────────────
def sections(text: str, heading: str, pr_body: bool = False) -> list[list[tuple[int, str]]]:
    """Every section headed by exactly `heading`, as its (1-based line no, line) pairs outside code fences. A
    section ends at the next `## ` heading or the end of the text — and, in a PR body (`pr_body`), at the footer
    line (`session `<name>``) or an HTML comment line too; a fenced block inside it is skipped whole, so command
    output never reads as a line."""
    out: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] | None = None
    fence = 0
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if fence:
            if re.fullmatch(rf"`{{{fence},}}", stripped):
                fence = 0
            continue
        m = FENCE_RE.match(stripped)
        if m:
            fence = len(m.group(1))
            continue
        if stripped == heading:
            cur = []
            out.append(cur)
            continue
        if cur is not None and (line.startswith("## ") or (pr_body and (stripped.startswith("<!--") or FOOTER_RE.match(stripped)))):
            cur = None
            continue
        if cur is not None and stripped:
            cur.append((i, line))
    return out


# ── parse ────────────────────────────────────────────────────────────────────────────────────
def parse_ticket(text: str) -> tuple[list[Criterion], list[str]]:
    """(the criteria in file order, the problems). A problem is a checkbox line in a `## Done when` section that
    is not criterion-shaped, or an id defined twice; a section with no checkbox line adds nothing."""
    crits: list[Criterion] = []
    problems: list[str] = []
    seen: set[str] = set()
    for sec in sections(text, DONE_WHEN_HEAD):
        for lineno, line in sec:
            if not BOX_RE.match(line):
                continue
            m = CRITERION_RE.match(line)
            if m is None:
                problems.append(f"{lineno}: not `- [ ] DW-<n> <criterion> · check: <check>`: {line.strip()[:80]}")
                continue
            c = Criterion(int(m.group("n")), m.group("crit"), m.group("check"))
            if c.id in seen:
                problems.append(f"{lineno}: {c.id} is defined twice")
                continue
            seen.add(c.id)
            crits.append(c)
    return crits, problems


# ── coverage ─────────────────────────────────────────────────────────────────────────────────
def ids_in(section_lines: list[tuple[int, str]], needs_evidence: bool) -> set[str]:
    """The ids the section's covering lines name. In `## Verified` the line must carry an evidence item
    (`needs_evidence`); in `## One honest limit` any non-empty reason after ` · ` does."""
    found: set[str] = set()
    for _, line in section_lines:
        m = IDS_LINE_RE.match(line.strip())
        if m is None:
            continue
        if needs_evidence and not ec.has_evidence(line.strip().lstrip("-* ").strip()):
            continue
        found.update(ID_RE.findall(m.group("ids")))
    return found


def coverage(crits: list[Criterion], body: str) -> tuple[list[str], list[str], list[str]]:
    """(gap ids, ids covered only by a limit line, ids the body cites that the ticket never defined)."""
    proven: set[str] = set()
    for sec in sections(body, VERIFIED_HEAD, pr_body=True):
        proven |= ids_in(sec, needs_evidence=True)
    limited: set[str] = set()
    for sec in sections(body, LIMIT_HEAD, pr_body=True):
        limited |= ids_in(sec, needs_evidence=False)
    defined = [c.id for c in crits]
    gap = [i for i in defined if i not in proven and i not in limited]
    with_limit = [i for i in defined if i not in proven and i in limited]
    unknown = sorted((proven | limited) - set(defined), key=lambda s: int(s[3:]))
    return gap, with_limit, unknown


# ── probe ────────────────────────────────────────────────────────────────────────────────────
def probe(c: Criterion, repo_dir: str, timeout: int) -> str:
    if c.command is None:
        return f"{c.id} manual"
    try:
        p = subprocess.run(c.command, shell=True, cwd=repo_dir, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"{c.id} timeout"
    return f"{c.id} passing" if p.returncode == 0 else f"{c.id} failing (exit {p.returncode})"


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def load_ticket(arg: str) -> tuple[list[Criterion] | None, int]:
    """(criteria, 0), or (None, the exit code) after printing why: 1 no criteria, 2 a malformed block."""
    crits, problems = parse_ticket(fsutil.read_arg(arg))
    if problems:
        for p in problems:
            print(f"done_when: {p}", file=sys.stderr)
        return None, 2
    if not crits:
        print("done_when: no DW-<n> criteria in the ticket", file=sys.stderr)
        return None, 1
    return crits, 0


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_parse = sub.add_parser("parse", help="list the ticket's criteria")
    p_parse.add_argument("file", help="the ticket text, or - for stdin")
    p_cov = sub.add_parser("coverage", help="which criteria a PR body covers")
    p_cov.add_argument("--ticket", required=True, help="the ticket text, or - for stdin")
    p_cov.add_argument("--body", required=True, help="the PR body file, or - for stdin")
    p_probe = sub.add_parser("probe", help="run each command check and report passing/failing")
    p_probe.add_argument("--ticket", required=True, help="the ticket text, or - for stdin")
    p_probe.add_argument("--repo-dir", required=True, help="the checkout the commands run in")
    p_probe.add_argument("--timeout", type=int, default=DEFAULT_PROBE_TIMEOUT, help="seconds per command")
    a = ap.parse_args(argv)

    crits, rc = load_ticket(a.file if a.cmd == "parse" else a.ticket)
    if crits is None:
        return rc
    if a.cmd == "parse":
        for c in crits:
            print(f"{c.id}\t{'command' if c.command else 'manual'}\t{c.command or c.check}\t{c.text}")
        return 0
    if a.cmd == "probe":
        for c in crits:
            print(probe(c, a.repo_dir, a.timeout))
        return 0
    gap, with_limit, unknown = coverage(crits, fsutil.read_arg(a.body))
    for i in with_limit:
        print(f"done_when: {i} is covered by a limit line, not proven", file=sys.stderr)
    for i in unknown:
        print(f"done_when: {i} is cited but the ticket defines no such criterion", file=sys.stderr)
    if gap:
        print(f"DONE WHEN: gap {', '.join(gap)}")
        return 3
    print("DONE WHEN: covered")
    return 0


def main(argv: list[str]) -> int:
    try:
        return _main(argv)
    except OSError as e:
        print(f"done_when: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
