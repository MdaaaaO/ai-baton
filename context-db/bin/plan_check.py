#!/usr/bin/env python3
"""plan_check.py — the mechanical refusals of a drafted epic plan (skills/epic-decompose § 3 and § 4).

Reads the child drafts `epic-decompose` writes (`child-<n>.md`, each with its `**Area**` and `**Wave**` lines) and the caps,
and prints one line per refusal; it changes nothing. The child's number is the `<n>` of its file name, the `#<n>` the
refusal lines and the `depends on #<n>` text of the other drafts use.

  **Area** — `<path>`, `<dir>/`, `+<new path>`
  **Wave** — <w>; depends on #<a> and #<b>        (or: <w>; no dependency)

Usage:
  plan_check.py [--key <epic> | --worker-limit <n> [--money-left <usd>]] [--cap <n>=<usd> ...] <child-n.md> ...

  --key           read the worker limit and the money left from the epic's task ledger, through the swarm module
  --worker-limit  the most children one wave holds; with no ledger, or to override it
  --money-left    the epic cap less what is spent, less the unspent caps of the unmerged children outside this plan
                  (§ 4); with no ledger, or to override it. Without it and without a capped ledger the caps are not checked
  --cap <n>=<usd> the cap the plan gives child <n>; repeat per child

The four refusals, in this order (an area entry overlaps another when it equals it or is a directory prefix of it, a
leading `+` ignored, no glob; two children may overlap when one depends on the other, directly or through others):
  OVERLAP #<a> #<b> <path>            two children of one wave share the path, with no dependency between them
  NO AREA #<a>                        a child with no area in a wave of two or more
  WIDE WAVE <w> <n> > <limit>         wave <w> holds <n> children, more than the worker limit
  OVER BUDGET $<sum> > $<left>        the caps add up past the money left

Prints `OK <n> children, <w> waves` and exits 0 when nothing is refused.
Exit codes: 0 nothing refused; 2 bad usage or a draft or ledger that cannot be read; 3 at least one refusal. Stdlib only.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CHILD_RE = re.compile(r"^child-(\d+)\.md$")
AREA_RE = re.compile(r"^\*\*Area\*\*\s*[—-]\s*(.*)$", re.M)
WAVE_RE = re.compile(r"^\*\*Wave\*\*\s*[—-]\s*([^;\s]+)\s*(?:;(.*))?$", re.M)
CODE_RE = re.compile(r"`([^`]+)`")
DEP_RE = re.compile(r"#(\d+)")


class PlanError(Exception):
    """Bad usage or an unreadable draft: exit 2."""


def parse_child(path: Path) -> dict:
    m = CHILD_RE.match(path.name)
    if not m:
        raise PlanError(f"{path}: the file name must be child-<n>.md")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PlanError(f"{path}: {e.strerror or e}") from e
    wave = WAVE_RE.search(text)
    if not wave:
        raise PlanError(f"{path}: no **Wave** line")
    area = AREA_RE.search(text)
    return {"n": int(m.group(1)), "wave": wave.group(1),
            "deps": {int(d) for d in DEP_RE.findall(wave.group(2) or "")},
            "area": CODE_RE.findall(area.group(1)) if area else []}


def bare(entry: str) -> str:
    return entry[1:] if entry.startswith("+") else entry


def shared(a: list[str], b: list[str]) -> str | None:
    """The first path two areas share: an entry equal to, or a directory prefix of, an entry of the other."""
    for x in map(bare, a):
        for y in map(bare, b):
            if x == y or (x.endswith("/") and y.startswith(x)):
                return y
            if y.endswith("/") and x.startswith(y):
                return x
    return None


def reach(children: dict[int, dict]) -> dict[int, set[int]]:
    """Each child's dependencies, direct and through others."""
    out: dict[int, set[int]] = {}
    for n in children:
        seen: set[int] = set()
        todo = [d for d in children[n]["deps"]]
        while todo:
            d = todo.pop()
            if d in seen or d not in children:
                continue
            seen.add(d)
            todo.extend(children[d]["deps"])
        out[n] = seen
    return out


def money(value: float) -> str:
    return f"{value:g}"


def check(children: dict[int, dict], limit: int, caps: dict[int, float], left: float | None) -> list[str]:
    deps = reach(children)
    waves: dict[str, list[int]] = {}
    for n in sorted(children):
        waves.setdefault(children[n]["wave"], []).append(n)
    lines = []
    for w, members in waves.items():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if b in deps[a] or a in deps[b]:
                    continue
                path = shared(children[a]["area"], children[b]["area"])
                if path:
                    lines.append(f"OVERLAP #{a} #{b} {path}")
        if len(members) >= 2:
            lines += [f"NO AREA #{n}" for n in members if not children[n]["area"]]
        if len(members) > limit:
            lines.append(f"WIDE WAVE {w} {len(members)} > {limit}")
    total = sum(caps.get(n, 0) for n in children)
    if left is not None and total > left:
        lines.append(f"OVER BUDGET ${money(total)} > ${money(left)}")
    return lines


def ledger_values(key: str, plan: set[int]) -> tuple[int, float | None]:
    import swarm
    try:
        led = swarm.load(key)
    except swarm.SwarmError as e:
        raise PlanError(f"{key}: {e}") from e
    spent, epic = swarm.epic_spend(led)
    if epic is None:
        return swarm.worker_limit(led), None
    held = 0.0
    for t, row in led["tickets"].items():
        if row["status"] in swarm.BEFORE_MERGED and not (t.isdigit() and int(t) in plan):
            cap = swarm.row_cap(led, row)
            held += max(cap - (row.get("spent") or 0), 0) if cap is not None else 0
    return swarm.worker_limit(led), epic - spent - held


def parse_cap(text: str) -> tuple[int, float]:
    try:
        n, usd = text.split("=", 1)
        return int(n.lstrip("#")), float(usd.lstrip("$"))
    except ValueError as e:
        raise PlanError(f"--cap {text!r}: want <n>=<usd>") from e


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="plan_check.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--key")
    p.add_argument("--worker-limit", type=int)
    p.add_argument("--money-left", type=float)
    p.add_argument("--cap", action="append", default=[])
    p.add_argument("children", nargs="+", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        children = {}
        for path in a.children:
            c = parse_child(path)
            if c["n"] in children:
                raise PlanError(f"{path}: child #{c['n']} given twice")
            children[c["n"]] = c
        limit, left = a.worker_limit, a.money_left
        if a.key:
            ledger_limit, ledger_left = ledger_values(a.key, set(children))
            limit = ledger_limit if limit is None else limit
            left = ledger_left if left is None else left
        if limit is None:
            raise PlanError("no worker limit: give --key or --worker-limit")
        caps = dict(parse_cap(c) for c in a.cap)
    except PlanError as e:
        print(f"plan_check: {e}", file=sys.stderr)
        return 2
    lines = check(children, limit, caps, left)
    for line in lines:
        print(line)
    if lines:
        return 3
    print(f"OK {len(children)} children, {len({c['wave'] for c in children.values()})} waves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
