#!/usr/bin/env python3
"""digest.py — the swarm digest of docs/swarm.md § The swarm tick, step 9, rendered from the ledger.

Six sections, in this order, then the operator's four lines (docs/operator-brief.md): shipped, blocked (with who must act),
decisions open, spend against caps, stalls, next. The text is a pure function of the ledger and the clock: the same ledger
and `--now` print the same digest. `render`, `due` and `show` read and write nothing; `posted` is the main session's one
write, after the digest is on the tracker.

Usage:
  digest.py render <key> [--now <iso>] [--json]   the digest (or its sections as JSON)
  digest.py due <key> [--now <iso>] [--wave-end]  `due` or `not due: next <iso>`; exit 0 either way. Due when no digest was
                                                  posted yet, when `swarm.digest_hours` (default 4) have passed, or at a wave end
  digest.py posted <key> [--now <iso>]            reads the posted text on stdin and records time and text in
                                                  state/swarm/<key>/digest.json, which the mission-control swarm panel reads

The first line carries the marker `<!-- swarm-digest -->`, so `swarm-inbox` drops the swarm's own digest comment.
Exit codes: 0 ok; 1 no ledger; 2 bad usage. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "context-db", "bin"))
import fsutil  # noqa: E402
import kit_profile as profile  # noqa: E402
import swarm  # noqa: E402
import swarm_budget  # noqa: E402
import swarm_msg  # noqa: E402

MARK = "<!-- swarm-digest -->"
SECTIONS = ("Shipped", "Blocked", "Decisions open", "Spend", "Stalls", "Next")
DONE = ("merged", "verified", "done")
DEFAULT_HOURS = 4


def ref(t: str) -> str:
    return swarm.ticket_ref(t)


def hours() -> float:
    v = profile.get("swarm.digest_hours", DEFAULT_HOURS)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else DEFAULT_HOURS


def digest_path(key: str):
    return swarm_msg.swarm_dir(key) / "digest.json"


def last_posted(key: str) -> dict | None:
    try:
        d = json.loads(digest_path(key).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("at") else None
    except (OSError, ValueError):
        return None


def sections(led: dict, now: datetime) -> dict:
    rep = swarm.tick_report(led, now)
    budget = swarm_budget.assess(led, now)
    rows = led["tickets"]
    shipped = [f"{ref(t)} {r['title'] or ''}".strip() + (f" (PR #{r['pr']}, {r['status']})" if r.get("pr") else f" ({r['status']})")
               for t, r in rows.items() if r["status"] in DONE]
    blocked = []
    for t, r in rows.items():
        if r["status"] == "blocked" and r.get("blocked"):
            blocked.append(f"{ref(t)}: {r['blocked'].get('reason')}; {r['blocked'].get('who')} must act")
        elif r["status"] == "parked":
            blocked.append(f"{ref(t)}: parked, {r.get('parked_reason') or 'no reason recorded'}; the orchestrator must re-plan it")
    for why in rep["no_assign"]:
        blocked.append(f"no new assignment: {why}; the owner must act" if "plan" in why or "STOP" in why or "budget" in why
                       else f"no new assignment: {why}")
    for f in rep["failed"]:
        blocked.append(f"{ref(f['ticket'])}: failed on the merged commit; a new assignment must fix it")
    for h in rep["holds"]:
        blocked.append(f"HOLD {h['scope']}: {h.get('reason') or 'no reason recorded'}; {h.get('releaser') or 'the releaser'} must lift it")
    decisions = [f"{d['id']}: {d['question']}" + (f" ({ref(d['ticket'])})" if d.get("ticket") else "") for d in rep["decisions_open"]]
    spend = swarm_budget.digest_lines(led, budget)
    stalls = [f"{ref(s['ticket'])} {s['worker']}: {s['minutes']} min, stall {s['stalls']}; next {s['next']}" for s in rep["stalled"]]
    stalls += [f"{ref(r['ticket'])} {r['worker']}: re-brief open since {r['since']}" for r in rep["rebriefs_open"]]
    nxt = [f"assign {', '.join(ref(t) for t in rep['assignable'])}"] if rep["assignable"] else []
    nxt += [f"integrate {ref(i['ticket'])} ({i['status']})" for i in rep["integrate"]]
    nxt += [f"{ref(w['ticket'])} waits on {', '.join(ref(d) for d in w['on'])}" for w in rep["waiting"]]
    nxt += [f"watch {w['id']}: {w['what']}" for w in rep["watches_due"]]
    return {"Shipped": shipped, "Blocked": blocked, "Decisions open": decisions, "Spend": spend, "Stalls": stalls, "Next": nxt,
            "_rep": rep, "_budget": budget}


def operator_lines(led: dict, sec: dict) -> list[str]:
    """Four lines of at most 20 words, from the same ledger reading as the sections."""
    rep, budget = sec["_rep"], sec["_budget"]
    shipped, decisions = len(sec["Shipped"]), len(sec["Decisions open"])
    plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"  # noqa: E731
    can = f"Open the {plural(shipped, 'shipped PR')}." if shipped else "Nothing."
    if not rep["plan_approved"]:
        diff = "Approve the plan. Nothing is assigned before your go."
    elif rep["no_assign"]:
        diff = f"Clear this first: {rep['no_assign'][0]}. Nothing new is assigned before then."
    elif decisions:
        diff = f"Answer the {plural(decisions, 'open decision')}. The ticket behind each one waits."
    else:
        diff = "Nothing."
    check = f"`swarm.py show {led['key']}` → one row per ticket, wave {rep['wave'] or '-'}."
    unread = len(budget.get("unread") or [])
    gap = f"Spend of {plural(unread, 'worker')} with no reading." if unread else "Work on any ticket that is not on the ledger."
    return [f"- Can now: {can}", f"- Do differently: {diff}", f"- Check with: {check}", f"- Not covered: {gap}"]


def render(led: dict, now: datetime) -> str:
    sec = sections(led, now)
    rep = sec["_rep"]
    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    head = (f"🟢 {day} — Swarm digest {led['key']}: {len(sec['Shipped'])} shipped, {len(sec['Blocked'])} blocked, "
            f"{len(sec['Decisions open'])} decisions open {MARK}")
    out = [head, ""]
    for name in SECTIONS:
        out.append(f"**{name}**")
        body = sec[name]
        out += [x if x.startswith("- ") else f"- {x}" for x in body] if body else ["- Nothing."]
        out.append("")
    out.append("**For the operator**")
    out += operator_lines(led, sec)
    return "\n".join(out) + "\n"


def cmd_render(a) -> int:
    led = swarm.load(a.key)
    now = swarm.parse_time(a.now) if a.now else datetime.now(timezone.utc)
    if a.json:
        sec = sections(led, now)
        print(json.dumps({"key": led["key"], **{k: v for k, v in sec.items() if not k.startswith("_")},
                          "operator": operator_lines(led, sec)}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(led, now))
    return 0


def cmd_due(a) -> int:
    swarm.load(a.key)
    now = swarm.parse_time(a.now) if a.now else datetime.now(timezone.utc)
    last = last_posted(a.key)
    if a.wave_end or last is None:
        print("due")
        return 0
    nxt = swarm.parse_time(last["at"]) + timedelta(hours=hours())
    print("due" if now >= nxt else f"not due: next {nxt.astimezone(timezone.utc).strftime(swarm.LEDGER_TIME)}")
    return 0


def cmd_posted(a) -> int:
    swarm.load(a.key)
    now = swarm.parse_time(a.now) if a.now else datetime.now(timezone.utc)
    text = sys.stdin.read()
    if not text.strip():
        print("digest: the posted text is empty", file=sys.stderr)
        return 2
    path = digest_path(a.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with fsutil.locked(str(path)):
        fsutil.atomic_write(str(path), json.dumps({"at": now.astimezone(timezone.utc).strftime(swarm.LEDGER_TIME), "text": text},
                                                  ensure_ascii=False, indent=2) + "\n")
    print(f"digest recorded at {now.astimezone(timezone.utc).strftime(swarm.LEDGER_TIME)}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="digest.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("render")
    s.add_argument("key")
    s.add_argument("--now")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("due")
    s.add_argument("key")
    s.add_argument("--now")
    s.add_argument("--wave-end", action="store_true")
    s.set_defaults(fn=cmd_due)
    s = sub.add_parser("posted")
    s.add_argument("key")
    s.add_argument("--now")
    s.set_defaults(fn=cmd_posted)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except swarm.Absent as e:
        print(f"digest: {e}", file=sys.stderr)
        return 1
    except swarm.SwarmError as e:
        print(f"digest: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
