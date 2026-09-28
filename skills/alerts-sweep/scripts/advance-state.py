#!/usr/bin/env python3
"""advance-state.py — apply one alerts-sweep pass's state update. The MAIN SESSION's job, never the
`alerts-sweep` fork's: the fork's `agent: triage` denies Edit/Write, and a raw shell rewrite or append in
its own body would be the same violation through Bash (kit_verify.py flags it — a read-only fork's body
citing a state-mutating shell form). This script does the two writes instead, atomically
(`fsutil.atomic_write`, no bare `open(...).write()` and no shell redirection), so the main session's part
stays one mechanical command, never a hand-edit:

  - advances `STATE`'s `**Last swept through:** ts \\`<ts>\\`` line to the newest ts the fork read, and its
    frontmatter `updated:` date to today,
  - appends zero or more recurrence-log lines to `KB` under `## Recurrence log` (created if missing).

Stdlib only, no `sed` — so the GNU/BSD `-i` divide (GNU allows `-i pattern`, BSD/macOS requires `-i ''`)
never comes up.

Usage: python3 advance-state.py --state <path> --kb <path> --ts <ts> [--recurrence <line> ...]
                                 [--root <dir>] [--reindex]

`--state`/`--kb` must be relative paths under `.context/` with no `..` segment — the same guard
alerts-sweep/SKILL.md step 0 applies before a path ever reaches the fork; this script re-checks it rather
than trust a caller. `--root` is the directory the two paths resolve against (the workspace root in real
use; a temp dir in tests). `--reindex` rebuilds the engine's document index afterward (the Makefile's
`index` target, scoped to `--root`'s own store — never the live one unless `--root` names it), the one
remaining step the pre-fix version ran inline; a test omits it to check the two writes alone.
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

_KIT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_KIT / "context-db" / "bin"))
import fsutil  # noqa: E402 — stdlib-only, ships with the kit

LAST_SWEPT = re.compile(r"(\*\*Last swept through:\*\* ts `)[0-9.]+(`)")
UPDATED = re.compile(r'^(updated:)\s*"?[0-9-]*"?\s*$', re.M)
RECURRENCE_HEADING = "## Recurrence log"


def safe_rel(raw: str) -> Path | None:
    """None unless `raw` is a relative path under `.context/` with no `..` segment."""
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts or p.parts[:1] != (".context",):
        return None
    return p


def advance_state_text(text: str, ts: str, today: str) -> str:
    new_text, n = LAST_SWEPT.subn(rf"\g<1>{ts}\g<2>", text, count=1)
    if n == 0:
        raise SystemExit("advance-state.py: no '**Last swept through:** ts `...`' line in the state file")
    new_text, n2 = UPDATED.subn(rf'\1 "{today}"', new_text, count=1)
    if n2 == 0:
        raise SystemExit("advance-state.py: no 'updated:' frontmatter line in the state file")
    return new_text


def append_recurrences_text(kb_text: str, lines: list[str]) -> str:
    """`kb_text` with one line per KNOWN alert appended under `## Recurrence log` (created, with one
    blank line before it, if the heading is missing) — plain lines, no blank line between entries, so a
    long-running KB reads as a log, not a series of paragraphs."""
    if not lines:
        return kb_text
    body = kb_text
    if body and not body.endswith("\n"):
        body += "\n"
    if RECURRENCE_HEADING not in body:
        if body and not body.endswith("\n\n"):
            body += "\n"
        body += f"{RECURRENCE_HEADING}\n\n"
    return body + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="advance-state.py")
    ap.add_argument("--state", required=True)
    ap.add_argument("--kb", required=True)
    ap.add_argument("--ts", required=True)
    ap.add_argument("--recurrence", action="append", default=[], help="one KB recurrence-log line; repeatable")
    ap.add_argument("--root", default=".", help="the two paths resolve against this directory (default: cwd)")
    ap.add_argument("--reindex", action="store_true", help="also rebuild the engine's index for --root's store")
    a = ap.parse_args(argv)

    state_rel, kb_rel = safe_rel(a.state), safe_rel(a.kb)
    if state_rel is None or kb_rel is None:
        print("advance-state.py: --state/--kb must be relative paths under .context/ with no '..' segment", file=sys.stderr)
        return 2

    root = Path(a.root)
    state_path, kb_path = root / state_rel, root / kb_rel
    if not state_path.is_file():
        print(f"advance-state.py: {state_path}: no such file", file=sys.stderr)
        return 2

    new_state = advance_state_text(state_path.read_text(encoding="utf-8"), a.ts, date.today().isoformat())
    fsutil.atomic_write(str(state_path), new_state)

    if a.recurrence:
        kb_text = kb_path.read_text(encoding="utf-8") if kb_path.is_file() else ""
        fsutil.atomic_write(str(kb_path), append_recurrences_text(kb_text, a.recurrence))

    if a.reindex:
        env = dict(os.environ, CONTEXT_ROOT=str((root / ".context").resolve()))
        p = subprocess.run(["make", "-s", "-C", str(_KIT / "context-db"), "index"], env=env,
                            capture_output=True, text=True)
        if p.returncode != 0:
            print(f"advance-state.py: index rebuild failed:\n{p.stderr}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
