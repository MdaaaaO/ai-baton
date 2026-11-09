#!/usr/bin/env python3
"""inbox.py — the swarm-inbox fork's deterministic half: what the swarm's local channels hold, and the item grammar.

The fork (`swarm-inbox`, read-only) reads the tracker and chat itself; this script reads the files, drops what the tick
must never see and prints one line per item:

  ITEM <kind> <scope> src=<source> ref=<ref> at=<utc> "<gist>"

kinds: line (a swarm grammar line, byte for byte: the quotes inside stay, the tick applies it; every other kind is
capped at 120 characters), delta (a human comment that adds scope), decision (a human question),
comment (any other human comment), pr-event, watch (a ledger watch that is due), chat, draft-sent, draft-edited,
draft-pending, draft-gone (a draft deleted from the thread). A STOP or HOLD line sorts first, then oldest first. `NO-OP` when there is nothing.

Usage:
  inbox.py local <key> [--now <iso>]       unread lines of orchestrator/in.ndjson and every worker's out.ndjson (past the
                                           `.read` mark, which it never moves), plus the ledger's due watches. Writes nothing.
                                           Ends with `READ-TO <lane>/<in|out>=<n> ...`: the record count it read each box to
  inbox.py mark <key> <lane>/<in|out>=<n>...  moves each `.read` mark to n records, never back: only what the sweep read,
                                           so a line that arrived meanwhile stays unread (the main session runs it)
  inbox.py since <key> [--refs]            the time the tracker was last swept through (`none` before the first sweep);
                                           --refs: the ids already taken at exactly that time, one per line
  inbox.py seen <key> --at <iso> [--refs <id,id>]
                                           moves that time to --at, never back (state/swarm/<key>/inbox.json), with the ids
                                           read at exactly that time; the main session runs it, the fork never does
  inbox.py chat <key>                      with `systems.slack` false: one line, `swarm-inbox: chat tier not applicable here`
                                           and exit 0; else one `DRAFT-OPEN` line per draft not yet read back
  inbox.py normalize [-]                   a JSON list of raw items on stdin (kind, scope, src, ref, at, gist, author,
                                           author_type) to ITEM lines: drops a bot (type, `[bot]` login or a configured bot) and the swarm's own
                                           digest comments, collapses a gist to one line of 120 characters at most (a `line` item is kept as it is)
  inbox.py draft-add --key <key> --channel <c> --thread <ts> --draft-id <id> --gist <text>
  inbox.py draft-close --key <key> <draft-id> --outcome sent|sent-edited|gone
                                           the two writers of `state/swarm/<key>/drafts.ndjson`; the main session runs them,
                                           the fork never does

The read marks move with `mark` (to what the sweep read); the ACK stays with `swarm_msg.py ack`. Exit codes: 0 ok; 2 bad usage. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "context-db", "bin"))
import fsutil  # noqa: E402
import kit_profile as profile  # noqa: E402
import swarm  # noqa: E402
import swarm_msg  # noqa: E402

KINDS = ("line", "delta", "decision", "comment", "pr-event", "watch", "chat", "draft-sent", "draft-edited", "draft-pending", "draft-gone")
URGENT = ("STOP", "HOLD")
GIST = 120
DIGEST_MARK = "<!-- swarm-digest"
CHAT_OFF = "swarm-inbox: chat tier not applicable here — slack is false"
OUTCOMES = ("sent", "sent-edited", "gone")


def one_line(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip().replace('"', "'")
    return t if len(t) <= GIST else t[: GIST - 1].rstrip() + "…"


def item(kind: str, scope: str, src: str, ref: str, at: str, gist: str) -> dict:
    # a swarm line is applied as it stands: only the newlines go, nothing is cut or re-quoted
    text = re.sub(r"\s*\n\s*", " ", str(gist or "")).strip() if kind == "line" else one_line(gist)
    return {"kind": kind, "scope": scope or "-", "src": src, "ref": ref or "-", "at": at or "-", "gist": text}


def render(it: dict) -> str:
    return f'ITEM {it["kind"]} {it["scope"]} src={it["src"]} ref={it["ref"]} at={it["at"]} "{it["gist"]}"'


def order(items: list[dict]) -> list[dict]:
    def key(it: dict):
        first = it["kind"] == "line" and it["gist"].split(" ", 1)[0] in URGENT
        return (0 if first else 1, it["at"])
    return sorted(items, key=key)


def is_bot(raw: dict, bots: set[str]) -> bool:
    who = str(raw.get("author") or "")
    return (str(raw.get("author_type") or "").lower() == "bot" or who.endswith("[bot]") or who.lower() in bots
            or DIGEST_MARK in str(raw.get("gist") or ""))


def read_mark(path, which: str) -> int:
    """The `.read` count, as `swarm_msg.py drain` reads it: a missing mark is 0, an unreadable, corrupt or negative one is
    refused (exit 2), never read as 0, which would hand the tick every old line again."""
    mark = path.with_name(f"{which}.read")
    try:
        seen = int(mark.read_text(encoding="utf-8").strip() or 0)
    except FileNotFoundError:
        return 0
    except (OSError, ValueError) as err:
        raise swarm_msg.MsgError(f"{mark} is not a readable line count ({err}): fix or delete it") from err
    if seen < 0:
        raise swarm_msg.MsgError(f"{mark} holds a negative count ({seen}): fix or delete it")
    return seen


def local_items(key: str, now: datetime, offsets: dict | None = None) -> list[dict]:
    out = []
    base = swarm_msg.swarm_dir(key)
    boxes = [("orchestrator", "in")]
    if base.is_dir():
        boxes += [(d.name, "out") for d in sorted(p for p in base.iterdir() if p.is_dir()) if d.name != "orchestrator"]
    for lane, which in boxes:
        path = swarm_msg.box(key, lane, which)
        recs = swarm_msg.read_records(path)
        if offsets is not None and len(recs) > read_mark(path, which):
            offsets[f"{lane}/{which}"] = len(recs)
        for rec in recs[read_mark(path, which):]:
            line = rec.get("text") or rec.get("line") or ""
            src = "orchestrator-in" if lane == "orchestrator" else f"out:{lane}"
            out.append(item("line", str(rec.get("scope") or rec.get("ticket") or "-"), src, str(rec.get("id") or "-"),
                            str(rec.get("at") or "-"), line))
    try:
        led = swarm.load(key)
    except swarm.Absent:
        led = None
    for w in (led or {}).get("watches", []):
        if not w.get("closed") and swarm.parse_time(w["due"]) <= now:
            out.append(item("watch", str(w.get("owner") or "-"), "ledger", str(w.get("id") or "-"), swarm.utc_iso(w["due"]),
                            w.get("what") or "watch due"))
    return order(out)


def drafts_path(key: str):
    return swarm_msg.swarm_dir(key) / "drafts.ndjson"


def open_drafts(key: str) -> list[dict]:
    opened: dict[str, dict] = {}
    for r in swarm_msg.read_records(drafts_path(key)):
        if r.get("event") == "open":
            opened[r["draft_id"]] = r
        elif r.get("event") == "close":
            opened.pop(r.get("draft_id"), None)
    return list(opened.values())


def cmd_local(a) -> int:
    now = swarm.parse_time(a.now) if a.now else datetime.now(timezone.utc)
    offsets: dict[str, int] = {}
    items = local_items(a.key, now, offsets)
    print("\n".join(render(i) for i in items) if items else "NO-OP")
    if offsets:
        print("READ-TO " + " ".join(f"{k}={n}" for k, n in sorted(offsets.items())))
    return 0


def cmd_mark(a) -> int:
    moved = []
    for spec in a.marks:
        m = re.fullmatch(r"([\w.-]+)/(in|out)=(\d+)", spec)
        if not m:
            print(f"inbox: not <lane>/<in|out>=<n>: {spec!r}", file=sys.stderr)
            return 2
        lane, which, n = m.group(1), m.group(2), int(m.group(3))
        path = swarm_msg.box(a.key, lane, which)
        mark = path.with_name(f"{which}.read")
        if not path.is_file():
            print(f"inbox: no such box: {lane}/{which}", file=sys.stderr)
            return 2
        with fsutil.locked(str(mark)):
            n = min(n, len(swarm_msg.read_records(path)))
            if n > read_mark(path, which):
                fsutil.atomic_write(str(mark), f"{n}\n")
                moved.append(f"{lane}/{which}={n}")
    print("marked " + " ".join(moved) if moved else "no change")
    return 0


def seen_path(key: str):
    return swarm_msg.swarm_dir(key) / "inbox.json"


def read_state(key: str) -> dict:
    try:
        d = json.loads(seen_path(key).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def read_since(key: str) -> str | None:
    return read_state(key).get("tracker_at")


def cmd_since(a) -> int:
    if a.refs:
        print("\n".join(str(r) for r in read_state(a.key).get("refs", [])))
    else:
        print(read_since(a.key) or "none")
    return 0


def cmd_seen(a) -> int:
    at = swarm.utc_iso(a.at)
    path = seen_path(a.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with fsutil.locked(str(path)):
        old = read_since(a.key)
        refs = [r for r in (a.refs or "").split(",") if r]
        if old and swarm.parse_time(old) > swarm.parse_time(at):
            print(f"no change: already swept through {old}")
            return 0
        if old and swarm.parse_time(old) == swarm.parse_time(at):
            # same instant: a comment that landed after the sweep still has to be read, so keep the ids taken
            refs = sorted(set(read_state(a.key).get("refs", [])) | set(refs))
            if refs == read_state(a.key).get("refs", []):
                print(f"no change: already swept through {old}")
                return 0
        fsutil.atomic_write(str(path), json.dumps({"tracker_at": at, "refs": refs}) + "\n")
    print(f"swept through {at}")
    return 0


def cmd_chat(a) -> int:
    if profile.get("systems.slack") is not True:
        print(CHAT_OFF)
        return 0
    drafts = open_drafts(a.key)
    for d in drafts:
        print(f'DRAFT-OPEN draft={d["draft_id"]} channel={d["channel"]} thread={d["thread_ts"]} at={d["at"]} "{one_line(d["gist"])}"')
    if not drafts:
        print("NO-DRAFTS")
    return 0


def cmd_normalize(a) -> int:
    text = sys.stdin.read() if a.file in (None, "-") else open(a.file, encoding="utf-8").read()
    try:
        raws = json.loads(text or "[]")
    except ValueError as e:
        print(f"inbox: not JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(raws, list):
        print("inbox: expected a JSON list", file=sys.stderr)
        return 2
    bots = {b.lower() for b in profile.review_facts()["bots"]}
    items = []
    for raw in raws:
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
            print(f"inbox: dropped a raw item with kind {raw.get('kind') if isinstance(raw, dict) else raw!r}", file=sys.stderr)
            continue
        if is_bot(raw, bots):
            continue
        items.append(item(raw["kind"], str(raw.get("scope") or "-"), str(raw.get("src") or "-"), str(raw.get("ref") or "-"),
                          str(raw.get("at") or "-"), str(raw.get("gist") or "")))
    items = order(items)
    print("\n".join(render(i) for i in items) if items else "NO-OP")
    return 0


def cmd_draft_add(a) -> int:
    rec = {"event": "open", "draft_id": a.draft_id, "channel": a.channel, "thread_ts": a.thread,
           "gist": one_line(a.gist), "at": swarm.now_iso()}
    path = drafts_path(a.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with fsutil.locked(str(path)):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"draft {a.draft_id} recorded")
    return 0


def cmd_draft_close(a) -> int:
    if a.draft_id not in {d["draft_id"] for d in open_drafts(a.key)}:
        print(f"no change: draft {a.draft_id} is not open")
        return 0
    path = drafts_path(a.key)
    with fsutil.locked(str(path)):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "close", "draft_id": a.draft_id, "outcome": a.outcome, "at": swarm.now_iso()}) + "\n")
    print(f"draft {a.draft_id} closed: {a.outcome}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="inbox.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("local")
    s.add_argument("key")
    s.add_argument("--now")
    s.set_defaults(fn=cmd_local)
    s = sub.add_parser("since")
    s.add_argument("key")
    s.add_argument("--refs", action="store_true")
    s.set_defaults(fn=cmd_since)
    s = sub.add_parser("seen")
    s.add_argument("key")
    s.add_argument("--at", required=True)
    s.add_argument("--refs")
    s.set_defaults(fn=cmd_seen)
    s = sub.add_parser("mark")
    s.add_argument("key")
    s.add_argument("marks", nargs="+")
    s.set_defaults(fn=cmd_mark)
    s = sub.add_parser("chat")
    s.add_argument("key")
    s.set_defaults(fn=cmd_chat)
    s = sub.add_parser("normalize")
    s.add_argument("file", nargs="?")
    s.set_defaults(fn=cmd_normalize)
    s = sub.add_parser("draft-add")
    s.add_argument("--key", required=True)
    s.add_argument("--channel", required=True)
    s.add_argument("--thread", required=True)
    s.add_argument("--draft-id", required=True)
    s.add_argument("--gist", required=True)
    s.set_defaults(fn=cmd_draft_add)
    s = sub.add_parser("draft-close")
    s.add_argument("--key", required=True)
    s.add_argument("draft_id")
    s.add_argument("--outcome", required=True, choices=OUTCOMES)
    s.set_defaults(fn=cmd_draft_close)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (swarm_msg.MsgError, swarm.SwarmError) as e:
        print(f"inbox: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
