#!/usr/bin/env python3
"""carousel_stats.py — how often sessions use the carousel versus a prose ask, as a table of numbers.

For each of the N most recent main-session transcripts (`transcripts.py` finds them) it counts
  calls  `AskUserQuestion` tool calls
  asks   turns whose final reply ends on a prose ask or offer (`carousel_check.kind`) with no call in that turn,
         split by kind: question (Q), offer (O), waits-list (W)
and the turns and each of those split before and after the session's first compaction (turns that began after a
`compact_boundary` line). The table is numbers only: rows are `S1`, `S2`, … in recency order, then a totals row.
No session id, path, date, project or transcript text is ever printed.

    carousel_stats.py [--latest N]     # default 12

Stdlib only.
"""
from __future__ import annotations
import argparse
import os
import sys

import carousel_check  # same dir — the one definition of a prose ask
import transcripts  # same dir — the shared transcript reader

COLS = ("turns", "turns_pre", "turns_post", "calls", "asks", "Q", "O", "W", "calls_pre", "asks_pre", "calls_post", "asks_post", "compacted")
KIND_COL = {"question": "Q", "offer": "O", "waits-list": "W"}


def main_transcripts() -> list[str]:
    """Every main-session transcript (no subagent files), most recently written first."""
    sub = os.sep + "subagents" + os.sep
    files = [p for p in transcripts.all_transcripts() if sub not in p]
    return sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)


def count(path: str) -> dict[str, int] | None:
    """One session's counts, or None when it holds no user turn."""
    row = dict.fromkeys(COLS, 0)
    try:
        for t in carousel_check.turns(transcripts.records(path)):
            post = "post" if t["after_compact"] else "pre"
            row["turns"] += 1
            row["turns_" + post] += 1
            row["compacted"] |= int(t["after_compact"])
            row["calls"] += t["carousel"]
            row["calls_" + post] += t["carousel"]
            k = "" if t["carousel"] else carousel_check.kind(t["final"])
            if k:
                row["asks"] += 1
                row["asks_" + post] += 1
                row[KIND_COL[k]] += 1
    except OSError:
        return None
    return row if row["turns"] else None


def table(rows: list[dict[str, int]]) -> str:
    total = {c: sum(r[c] for r in rows) for c in COLS}
    out = ["| row | " + " | ".join(COLS) + " |", "|---|" + "---|" * len(COLS)]
    for i, r in enumerate(rows, 1):
        out.append(f"| S{i} | " + " | ".join(str(r[c]) for c in COLS) + " |")
    out.append("| total | " + " | ".join(str(total[c]) for c in COLS) + " |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--latest", type=int, default=12, help="how many recent sessions to count (default 12)")
    a = p.parse_args(argv)
    rows: list[dict[str, int]] = []
    for path in main_transcripts():
        r = count(path)
        if r is not None:
            rows.append(r)
        if len(rows) >= a.latest:
            break
    if not rows:
        sys.stderr.write("carousel_stats: no session transcript found\n")
        return 3
    print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
