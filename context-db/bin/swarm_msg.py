#!/usr/bin/env python3
"""swarm_msg.py — the swarm message grammar of docs/swarm.md § Message grammar as code, and the per-worker inbox and outbox.

One line per message: `<WORD> <scope> [<field>…] v=<n> id=<id> from=<lane> [reason="…"] [note="…"]`. `format` writes a
line and refuses a free-text one, `parse` reads it back; `send`, `drain`, `ack` and `pending` keep the record on disk, in
`.context/state/swarm/<key>/<worker>/in.ndjson` (the inbox) and `out.ndjson` (the outbox), and in
`.context/state/swarm/<key>/orchestrator/in.ndjson` for the orchestrator. `SendMessage` is only the wake-up.

Usage:
  swarm_msg.py format <WORD> [<arg>…] --from <lane> [--id <id>] [--key <key>] [--json] [--<field> <value>…]
      Prints the line (or with --json its record). Positional args and `--<field>` follow the word's row in the table:
      `format READY 123 a1b2c3d --from worker-a`, `format ASSIGN 123 brief.md --from orchestrator --rebrief 1`.
      `NEEDS`, `DECISION` and `Assumed:` carry their own text (one arg) and are sent as a record, so `v`, `id` and
      `from` are fields of it, not part of the text. A new id is checked against --key's files, and a --id re-send keeps it.
  swarm_msg.py parse <line | record | ->
      Prints the line as JSON: `word`, `ticket` (or `scope`), the line's fields, `v`, `id`, `from`. Free text, an unknown
      word, a missing field and a grammar version it does not read are refused (exit 2), naming what is accepted.
  swarm_msg.py send --key <key> <worker> <line | record | -> [--inbox]
      Appends one validated record to <worker>'s outbox, or with --inbox to its inbox.
      A second send of an id that box already holds appends one copy, marked `resent`, with a fresh `at`; a further one
      changes nothing. The copy sits after the read mark, so `drain` shows the line again.
  swarm_msg.py drain --key <key> <worker> [--outbox] [--json]
      Prints <worker>'s unread inbox lines (the orchestrator drains an outbox with --outbox) and marks them read; the mark
      is `in.read` / `out.read` beside the file, never in it.
  swarm_msg.py ack --key <key> --as <lane> <id>…
      Writes `ACK <scope> re=<id>` into the inbox of the line's sender. A second ack of an id changes nothing.
  swarm_msg.py pending --key <key> [--minutes <n>] [--now <iso>]
      Lists each id sent and not acked by its receiver, one window (`swarm.ack_minutes`, default 20) after it was sent:
      `<id> <word> <scope> to=<lane> age=<m> resend`. After a re-send (`send` of the id again) it is listed `unreachable`
      one window after the copy, and not before. A line to a background worker whose run ended is skipped: a `DONE` the
      orchestrator wrote into that worker's inbox after it, for the same ticket (or a line with no ticket), and a
      `MERGED` for the ticket of such a `DONE`, whichever came first. Prints nothing when none.

Rules that are this script's own call, not docs/swarm.md's: an id is 3 to 16 letters and digits; `from=` and `<who>` are
names; a required field is not empty; a value holds no newline or carriage return; a STOP or HOLD scope is a name or a
full ref `Owner/repo#N`.

Which lane is the orchestrator: an address you type (`send`, `drain`, `ack --as`) resolves against live registry rows only
(not `status: ended`, not `state: retired`), so an ended orchestrator's name is an ordinary lane. A sender read from a
stored line resolves against every row, so after a handoff the predecessor's ACK and DONE still count as the
orchestrator's. Known leftover: a lane that was once an orchestrator and later acks as a worker in the same key reads as
the orchestrator in `pending`, so it shows a false `resend`, never a misroute.

Exit codes: 0 ok; 1 absent (`ack`: no such id); 2 bad usage, a refused line or an I/O error. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import secrets
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontmatter  # noqa: E402  — same dir
import kit_profile as profile  # noqa: E402  — same dir
from fsutil import atomic_write, locked  # noqa: E402  — same dir

GRAMMAR_VERSIONS = ("1",)          # the current version last: a parser reads the current and the previous
CURRENT_VERSION = GRAMMAR_VERSIONS[-1]
ORCHESTRATOR = "orchestrator"
DEFAULT_ACK_MINUTES = 20
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA_RE = re.compile(r"[0-9a-f]{7,64}")
ID_RE = re.compile(r"[A-Za-z0-9]{3,16}")
KEY_VALUE_RE = re.compile(r"([a-z_]+)=(.*)", re.S)
BLOCKED_REASONS = ("budget", "stop", "lease", "scope", "input")


class MsgError(Exception):
    """A refused line, bad usage or a failed read: exit 2."""
    code = 2


class Absent(MsgError):
    """No such id: exit 1."""
    code = 1


def _name(v: str) -> str:
    if not NAME_RE.fullmatch(v):
        raise MsgError(f"bad name {v!r}: letters, digits, `.`, `_` and `-`, starting with a letter or digit")
    return v


def _ticket(v: str) -> str:
    return _name(v.lstrip("#"))


def _sha(v: str) -> str:
    if not SHA_RE.fullmatch(v):
        raise MsgError(f"bad sha {v!r} (7 to 64 lowercase hex digits)")
    return v


def _number(v: str) -> str:
    v = v.lstrip("#")
    if not v.isdigit():
        raise MsgError(f"bad number {v!r}")
    return v


def _reason(v: str) -> str:
    if v not in BLOCKED_REASONS:
        raise MsgError(f"BLOCKED reason must be one of {', '.join(BLOCKED_REASONS)}, not {v!r}")
    return v


def _time(v: str) -> str:
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise MsgError(f"bad time {v!r}: an ISO 8601 time") from e
    return v


def _one(v: str) -> str:
    if v != "1":
        raise MsgError(f"rebrief is 1, not {v!r}")
    return v


def _text(v: str) -> str:
    if not v:
        raise MsgError("a required field is empty")
    return v


def _any(v: str) -> str:
    return v


REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*#[0-9]+")


def _scope(v: str) -> str:
    """A name, or a full ref `Owner/repo#N` (the scope `lease.py stop` takes)."""
    return v if REF_RE.fullmatch(v) else _name(v)


# word -> (positional (name, check), keyed fields written before `v=` as {name: (check, required)}). `reason` and `note`
# are written after `from=`. `ticket` as the first name marks a ticket-scoped line; `scope` any other.
LINES: dict[str, tuple[tuple, dict]] = {
    "ASSIGN": ((("ticket", _ticket), ("file", _text)), {"lease": (_name, False), "rebrief": (_one, False)}),
    "ACK": ((("scope", _scope),), {"re": (_name, True)}),
    "CLAIMED": ((("ticket", _ticket), ("branch", _text)), {}),
    "PR": ((("ticket", _ticket), ("branch", _text), ("sha", _sha)), {"body": (_text, True)}),
    "READY": ((("ticket", _ticket), ("sha", _sha)), {}),
    "MERGED": ((("ticket", _ticket), ("pr", _number), ("sha", _sha)), {}),
    "BLOCKED": ((("ticket", _ticket), ("reason", _reason), ("who", _name)), {"sha": (_sha, False), "ran": (_any, False)}),
    "DONE": ((("ticket", _ticket),), {}),
    "HOLD": ((("scope", _scope),), {"releaser": (_name, True)}),
    "RELEASE": ((("scope", _name),), {}),
    "STOP": ((("scope", _scope),), {}),
    "PARKED": ((("ticket", _ticket),), {}),
    "STANDBY": ((("scope", _name),), {"until": (_time, True)}),
    "RETIRED": ((("scope", _name),), {}),
}
AFTER_FROM = ("reason", "note")
REASON_REQUIRED = ("HOLD", "STOP", "PARKED")
REASON_ALLOWED = ("RELEASE", "RETIRED")
TEXT_LINES = ("NEEDS", "DECISION", "Assumed:")   # the text their own pages fix; v, id and from sit beside it
ALL_WORDS = (*LINES, *TEXT_LINES)


def _check_text_line(word: str, text: str) -> str:
    text = text.strip()
    if not text or "\n" in text or "\r" in text:
        raise MsgError(f"{word} takes one non-empty line")
    if word == "NEEDS":
        if not re.fullmatch(r"NEEDS [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+ \S+", text):
            raise MsgError(f"NEEDS is `NEEDS <system>.<kind> <name>` (docs/env-facts.md): {text!r}")
    elif word == "DECISION":
        parts = [p.strip() for p in text[len("DECISION"):].split("|")]
        if not text.startswith("DECISION ") or len(parts) < 3 or not all(parts):
            raise MsgError(f"DECISION is `DECISION <question> | <option> | <option>` (docs/carousel.md): {text!r}")
    elif not re.fullmatch(r"Assumed: .+ \(revisit: .+\)", text):
        raise MsgError(f"Assumed: is `Assumed: <one line> (revisit: <checkpoint>)` (docs/assumptions.md): {text!r}")
    return text


def _quote(v: str) -> str:
    if "\n" in v or "\r" in v:
        raise MsgError(f"a value is one line: {v!r}")
    if not v:
        return '""'
    if re.search(r"[\s\"'\\]", v):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def _word(line: str) -> str:
    return line.split(None, 1)[0] if line.split() else ""


def _refuse_word(word: str, line: str) -> MsgError:
    return MsgError(f"not a grammar line: {line!r}. The first word is one of {', '.join(ALL_WORDS)}; a message that is "
                    "not one of them is free text, and swarm_msg.py refuses it")


def new_id(taken: set[str]) -> str:
    while True:
        i = secrets.token_hex(3)
        if i not in taken:
            return i


def build(word: str, args: list[str], fields: dict[str, str], sender: str, msg_id: str | None = None,
          taken: set[str] = frozenset()) -> dict:
    """A record for one line: `word`, `line`, `v`, `id`, `from` and the line's fields. Refuses free text and a missing field."""
    sender = _name(sender)
    if msg_id is not None and not ID_RE.fullmatch(msg_id):
        raise MsgError(f"bad id {msg_id!r}: 3 to 16 letters and digits")
    mid = msg_id or new_id(set(taken))
    if word in TEXT_LINES:
        if len(args) != 1 or fields:
            raise MsgError(f"{word} takes its text as one argument and no fields; v, id and from are the record's")
        text = _check_text_line(word, args[0] if args[0].startswith(word) else f"{word} {args[0]}")
        return {"word": word, "line": text, "text": text, "v": CURRENT_VERSION, "id": mid, "from": sender}
    if word not in LINES:
        raise _refuse_word(word, word)
    names, keyed = LINES[word]
    if len(args) != len(names):
        raise MsgError(f"{word} takes {' '.join('<' + n + '>' for n, _ in names)}, got {len(args)} argument(s)")
    rec: dict = {"word": word}
    for (n, check), v in zip(names, args):
        if KEY_VALUE_RE.fullmatch(v):
            raise MsgError(f"{n} {v!r} reads as a key=value field")
        rec[n] = check(v)
    allowed = {*keyed, "note", *(("reason",) if word in (*REASON_REQUIRED, *REASON_ALLOWED) else ())}
    for k in fields:
        if k not in allowed:
            raise MsgError(f"{word} has no field {k}; it takes {', '.join(sorted(allowed))}")
    for k, (check, required) in keyed.items():
        if k in fields:
            if required and not fields[k]:
                raise MsgError(f"{word} needs {k}= and it is empty")
            rec[k] = check(fields[k])
        elif required:
            raise MsgError(f"{word} needs {k}=")
    if word in REASON_REQUIRED and not fields.get("reason"):
        raise MsgError(f"{word} needs reason=")
    for k in AFTER_FROM:
        if k in fields:
            rec[k] = fields[k]
    rec.update({"v": CURRENT_VERSION, "id": mid, "from": sender})
    rec["line"] = _render(word, names, keyed, rec)
    return rec


def _render(word, names, keyed, rec) -> str:
    parts = [word, *(_quote(rec[n]) for n, _ in names)]
    parts += [f"{k}={_quote(rec[k])}" for k in keyed if k in rec]
    parts += [f"v={rec['v']}", f"id={rec['id']}", f"from={rec['from']}"]
    parts += [f"{k}={_quote(rec[k])}" for k in AFTER_FROM if k in rec and k not in {n for n, _ in names}]
    return " ".join(parts)


def parse_line(line: str) -> dict:
    """One grammar line as a dict. For CLAIMED, PR, READY and BLOCKED the same fields `swarm.parse_status_line` returns:
    `word`, `ticket`, the line's own fields, `v`, `id`, `from`, and `body`, `sha`, `ran`, `note` when present."""
    line = line.strip()
    if "\n" in line or "\r" in line:
        raise MsgError(f"a line is one line, with no newline or carriage return in it: {line!r}")
    word = _word(line)
    if word in TEXT_LINES:
        raise MsgError(f"{word} carries v, id and from beside its text, as a record: parse the record, not the bare text")
    if word not in LINES:
        raise _refuse_word(word, line)
    try:
        parts = shlex.split(line)
    except ValueError as e:
        raise MsgError(f"not a grammar line ({e}): {line!r}") from e
    positional: list[str] = []
    keyed: dict[str, str] = {}
    for part in parts[1:]:
        m = KEY_VALUE_RE.fullmatch(part)
        if m:
            keyed[m.group(1)] = m.group(2)
        elif keyed:
            raise MsgError(f"a field after a key=value pair: {line!r}")
        else:
            positional.append(part)
    names, fields = LINES[word]
    if len(positional) != len(names):
        raise MsgError(f"{word} takes {' '.join('<' + n + '>' for n, _ in names)}: {line!r}")
    for need in ("v", "id", "from"):
        if not keyed.get(need):
            raise MsgError(f"no {need}= on the line: {line!r}")
    if keyed["v"] not in GRAMMAR_VERSIONS:
        raise MsgError(f"grammar version {keyed['v']} on the line; this script reads {', '.join(GRAMMAR_VERSIONS)}: {line!r}")
    if not ID_RE.fullmatch(keyed["id"]):
        raise MsgError(f"bad id {keyed['id']!r}: {line!r}")
    rec: dict = {"word": word}
    for (n, check), v in zip(names, positional):
        rec[n] = check(v)
    rec.update({"v": keyed["v"], "id": keyed["id"], "from": _name(keyed["from"])})
    allowed = {*fields, "note", *(("reason",) if word in (*REASON_REQUIRED, *REASON_ALLOWED) else ())}
    for k, v in keyed.items():
        if k in ("v", "id", "from"):
            continue
        if k not in allowed:
            raise MsgError(f"{word} has no field {k}: {line!r}")
        rec[k] = fields[k][0](v) if k in fields else v
    for k, (_, required) in fields.items():
        if required and not rec.get(k):
            raise MsgError(f"{word} needs {k}=, not empty: {line!r}")
    if word in REASON_REQUIRED and not rec.get("reason"):
        raise MsgError(f"{word} needs reason=: {line!r}")
    return rec


def parse_any(text: str) -> dict:
    """A grammar line, or a record (a JSON line, how `NEEDS`, `DECISION` and `Assumed:` travel): the record with `line`."""
    text = text.strip()
    if text.startswith("{"):
        try:
            raw = json.loads(text)
        except ValueError as e:
            raise MsgError(f"not a record ({e}): {text[:80]!r}") from e
        word = raw.get("word") if isinstance(raw, dict) else None
        if word in TEXT_LINES:
            rec = build(word, [str(raw.get("text") or raw.get("line") or "")], {}, str(raw.get("from") or ""), str(raw.get("id") or "") or None)
            if str(raw.get("v")) not in GRAMMAR_VERSIONS:
                raise MsgError(f"grammar version {raw.get('v')} on the record; this script reads {', '.join(GRAMMAR_VERSIONS)}")
            rec["v"] = str(raw["v"])
            return rec
        if isinstance(raw, dict) and isinstance(raw.get("line"), str):
            return {**parse_line(raw["line"]), "line": raw["line"].strip()}
        raise MsgError(f"a record is a JSON object with a `line` or a text word: {text[:80]!r}")
    return {**parse_line(text), "line": text}


# ── the files ───────────────────────────────────────────────────────────────────────────────

def swarm_dir(key: str) -> Path:
    return profile.context_root() / "state" / "swarm" / _name(key)


def orchestrator_names(live: bool = False) -> set[str]:
    """Every lane name the registry says is an orchestrator: the `lane` field of a row with `role: orchestrator`, and its
    file stem (the session name). `live` skips a row that is `status: ended` or `state: retired`."""
    names: set[str] = set()
    sessions = profile.context_root() / "sessions"
    try:
        rows = sorted(sessions.glob("*.md"))
    except OSError:
        return names
    for row in rows:
        try:
            parts = frontmatter.split(row.read_text(encoding="utf-8"))
            meta = frontmatter.parse_flat(parts[0]) if parts else {}
        except (OSError, ValueError):
            continue
        if live and (frontmatter.unquote(meta.get("status", "")) == "ended"
                     or frontmatter.unquote(meta.get("state", "")) == "retired"):
            continue
        if frontmatter.unquote(meta.get("role", "")) == ORCHESTRATOR:
            names.update({row.stem, frontmatter.unquote(meta.get("lane", ""))} - {""})
    return names


def lane_dir(lane: str, names: set[str] | None = None, live: bool = True) -> str:
    """The directory a lane's files live in: `orchestrator` for the orchestrator, else the lane. The registry says which
    lane is the orchestrator (a row with `role: orchestrator`, by its `lane` field or its name). An address the caller
    types (`send`, `drain`, `ack --as`) resolves against live rows only, so an ended orchestrator's name is an ordinary
    lane with its own box; a sender read from a stored record passes `live=False` and resolves against every row, so a
    predecessor's ACK and DONE still count as the orchestrator's after a handoff."""
    _name(lane)
    if lane == ORCHESTRATOR:
        return lane
    return ORCHESTRATOR if lane in (orchestrator_names(live) if names is None else names) else lane


def box(key: str, worker: str, which: str) -> Path:
    return swarm_dir(key) / _name(worker) / f"{which}.ndjson"


def read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except ValueError as e:
            raise MsgError(f"{path} line {n} is not a record: {e}") from e
    return out


def all_boxes(key: str) -> list[tuple[str, str, list[dict]]]:
    """(worker dir, `in` or `out`, its records) for every file of the epic."""
    base = swarm_dir(key)
    out = []
    if base.is_dir():
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            for which in ("in", "out"):
                out.append((d.name, which, read_records(d / f"{which}.ndjson")))
    return out


def taken_ids(key: str) -> set[str]:
    return {r.get("id", "") for _, _, recs in all_boxes(key) for r in recs}


def append(key: str, worker: str, which: str, rec: dict, to: str) -> str:
    """Append one record. `sent` for a new id; `resent` when that file already holds the id once (a copy with a fresh `at`
    and `resent` set); `no change` from the third send on."""
    path = box(key, worker, which)
    with locked(str(path)):
        held = [r for r in read_records(path) if r.get("id") == rec["id"]]
        if len(held) > 1:
            return "no change"
        path.parent.mkdir(parents=True, exist_ok=True)
        stored = {k: v for k, v in rec.items() if k != "text"}
        stored.update({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "to": to})
        if held:
            stored["resent"] = True
        if rec["word"] in TEXT_LINES:
            stored["text"] = rec["line"]
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(stored, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return "resent" if held else "sent"


def show(rec: dict, as_json: bool) -> str:
    if as_json:
        return json.dumps(rec, ensure_ascii=False)
    if rec["word"] in TEXT_LINES:
        return f"{rec['line']}\t[v={rec['v']} id={rec['id']} from={rec['from']}]"
    return rec["line"]


# ── commands ────────────────────────────────────────────────────────────────────────────────

def literal(arg: str) -> str:
    """The line itself, or stdin for `-` (a line is text, never a path)."""
    return sys.stdin.read() if arg == "-" else arg


def cmd_format(a) -> int:
    args, fields = [], {}
    opts = {"from": a.sender, "id": a.id, "key": a.key}
    rest = list(a.rest)
    while rest:
        t = rest.pop(0)
        if t == "--json":
            a.json = True
        elif t.startswith("--") and len(t) > 2:
            if not rest:
                raise MsgError(f"{t} needs a value")
            v = rest.pop(0)
            if t[2:] in opts:
                opts[t[2:]] = v
            else:
                fields[t[2:].replace("-", "_")] = v
        else:
            args.append(t)
    if not opts["from"]:
        raise MsgError("format needs --from <lane>")
    taken = taken_ids(opts["key"]) if opts["key"] else set()
    rec = build(a.word, args, fields, opts["from"], opts["id"], taken)
    print(json.dumps(rec, ensure_ascii=False) if a.json or a.word in TEXT_LINES else rec["line"])
    return 0


def cmd_parse(a) -> int:
    print(json.dumps(parse_any(literal(a.text)), ensure_ascii=False))
    return 0


def cmd_send(a) -> int:
    rec = parse_any(literal(a.line))
    which, to = ("in", lane_dir(a.worker)) if a.inbox else ("out", ORCHESTRATOR)
    result = append(a.key, lane_dir(a.worker), which, rec, to)
    note = f"no change: id {rec['id']} was sent twice already" if result == "no change" else result
    print(f"{note} {rec['id']} -> {lane_dir(a.worker)}/{which}")
    return 0


def cmd_drain(a) -> int:
    which = "out" if a.outbox else "in"
    path = box(a.key, lane_dir(a.worker), which)
    mark = path.with_name(f"{which}.read")
    with locked(str(mark)):
        try:
            seen = int(mark.read_text(encoding="utf-8").strip() or 0)
        except FileNotFoundError:
            seen = 0
        except (OSError, ValueError) as err:
            raise MsgError(f"{mark} is not a readable line count ({err}): fix or delete it") from err
        if seen < 0:
            raise MsgError(f"{mark} holds a negative count ({seen}): fix or delete it")
        recs = read_records(path)
        for rec in recs[seen:]:
            print(show(rec, a.json))
        if len(recs) > seen:
            atomic_write(str(mark), f"{len(recs)}\n")
    return 0


def cmd_ack(a) -> int:
    me = lane_dir(a.sender)
    boxes = all_boxes(a.key)
    acked = {(r.get("from"), r.get("re")) for _, _, recs in boxes for r in recs if r.get("word") == "ACK"}
    status = 0
    for target in a.ids:
        hit = next((r for _, _, recs in boxes for r in recs if r.get("id") == target and r.get("to") == me), None)
        if hit is None:
            print(f"no line {target} sent to {me}", file=sys.stderr)
            status = 1
            continue
        if hit["word"] == "ACK":
            raise MsgError(f"{target} is an ACK: nothing acks an ACK")
        if (a.sender, target) in acked:
            print(f"no change: {target} is already acked by {a.sender}")
            continue
        scope = hit.get("ticket") or hit.get("scope") or hit["from"]
        rec = build("ACK", [scope], {"re": target}, a.sender, taken=taken_ids(a.key))
        append(a.key, lane_dir(hit["from"], live=False), "in", rec, lane_dir(hit["from"], live=False))
        acked.add((a.sender, target))
        print(f"acked {target} -> {lane_dir(hit['from'], live=False)}/in")
    return status


def _minutes() -> int:
    v = profile.get("swarm.ack_minutes", DEFAULT_ACK_MINUTES)
    try:
        return int(v)
    except (TypeError, ValueError):
        return DEFAULT_ACK_MINUTES


def _when(rec: dict) -> datetime:
    return datetime.fromisoformat(rec["at"].replace("Z", "+00:00"))


def pending(key: str, minutes: int, now: datetime) -> list[dict]:
    """Ids sent and not acked by their receiver. A line is listed `resend` once a window has passed since it was sent; once
    it was re-sent (a second copy of the id in the box) it is listed `unreachable` a window after the copy, never before."""
    boxes = all_boxes(key)
    names = orchestrator_names()  # every row, ended too: a stored sender
    acked = {(lane_dir(r.get("from") or "orchestrator", names), r.get("re"))
             for _, _, recs in boxes for r in recs if r.get("word") == "ACK"}
    out = []
    for worker, which, recs in boxes:
        first: dict[str, int] = {}
        copy: dict[str, dict] = {}
        for i, r in enumerate(recs):
            if r.get("id") not in first:
                first[r.get("id")] = i
            elif r.get("id") not in copy:
                copy[r.get("id")] = r
        # a background worker whose run ended reads nothing and owes no ack: the orchestrator's DONE in its inbox says so
        dones = []
        if which == "in" and worker != ORCHESTRATOR:
            dones = [(i, r.get("ticket")) for i, r in enumerate(recs)
                     if r.get("word") == "DONE" and lane_dir(r.get("from") or "", names) == ORCHESTRATOR]
        for mid, i in first.items():
            r = recs[i]
            if r.get("word") == "ACK" or (r.get("to"), mid) in acked:
                continue
            ticket = r.get("ticket")
            if r.get("word") == "DONE" and any(d == i for d, _ in dones):
                continue
            if any(d > i and (ticket is None or t == ticket) for d, t in dones):
                continue
            if r.get("word") == "MERGED" and any(t == ticket for _, t in dones):
                continue
            age = (now - _when(r)).total_seconds() / 60
            if mid in copy:
                state = "unreachable" if (now - _when(copy[mid])).total_seconds() / 60 >= minutes else None
            else:
                state = "resend" if age >= minutes else None
            if state:
                out.append({"id": mid, "word": r["word"], "scope": ticket or r.get("scope") or "-",
                            "to": r.get("to", "?"), "age": int(age), "state": state})
    return out


def cmd_pending(a) -> int:
    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    for p in pending(a.key, a.minutes if a.minutes is not None else _minutes(), now):
        print(f"{p['id']} {p['word']} {p['scope']} to={p['to']} age={p['age']}m {p['state']}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="swarm_msg.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("format")
    f.add_argument("word")
    f.set_defaults(sender=None, id=None, key=None, json=False)
    f.add_argument("rest", nargs=argparse.REMAINDER)
    f.set_defaults(fn=cmd_format)
    p = sub.add_parser("parse")
    p.add_argument("text")
    p.set_defaults(fn=cmd_parse)
    s = sub.add_parser("send")
    s.add_argument("--key", required=True)
    s.add_argument("--inbox", action="store_true")
    s.add_argument("worker")
    s.add_argument("line")
    s.set_defaults(fn=cmd_send)
    d = sub.add_parser("drain")
    d.add_argument("--key", required=True)
    d.add_argument("--outbox", action="store_true")
    d.add_argument("--json", action="store_true")
    d.add_argument("worker")
    d.set_defaults(fn=cmd_drain)
    k = sub.add_parser("ack")
    k.add_argument("--key", required=True)
    k.add_argument("--as", dest="sender", required=True)
    k.add_argument("ids", nargs="+")
    k.set_defaults(fn=cmd_ack)
    g = sub.add_parser("pending")
    g.add_argument("--key", required=True)
    g.add_argument("--minutes", type=int)
    g.add_argument("--now")
    g.set_defaults(fn=cmd_pending)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except MsgError as e:
        print(f"swarm_msg: {e}", file=sys.stderr)
        return e.code
    except (OSError, ValueError) as e:
        print(f"swarm_msg: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
