#!/usr/bin/env python3
"""swarm.py — an epic's task ledger: one JSON file a session writes, one row per ticket.

`docs/swarm.md` is the contract. Its § What moves a row is the state machine: `MOVES` below is that table as data and
`tests/test_swarm.py` holds the two together. Nothing runs between sessions: the orchestrator calls this at a tick, the
verifier calls `pass`, and the file is the state. The `## Swarm` table in the epic's context doc is rendered from the
file, never the reverse.

Every subcommand takes the epic key first:
  init      create the ledger (--repo, --session, --waves, the budget caps, --max-workers)
  add       a row per ticket: --area, --dep, --wave, --sizing, --dw or --ticket-file, --cap-usd. A new row on an approved
            plan, or a changed area, dep, wave, sizing or cap, resets `plan_approved`, unless it is an --incident lane or
            --already-done bookkeeping
  approve   the owner's go on the plan, with its --decision line. `assign` refuses until then
  assign    a worker takes the row: --kind, --name, --ref, --worktree, --branch. --rebrief moves no row. Refused before the
            plan's go, under a STOP or HOLD, at a budget cap, past the worker cap, or with a dependency not done; an
            incident lane skips only the plan's go and the dependencies
  status    the orchestrator's moves: pr, ready-to-merge (--head), parked (--reason), merged (--sha), blocked (a FAIL on
            the merged commit, --reason), done. A move the table does not have is refused
  apply     one grammar line, CLAIMED, PR, READY or BLOCKED. A second copy of an id changes nothing. A line from a sender
            that is not the row's worker is recorded and moves nothing. DONE moves no row and is refused
  progress  a new commit, status or comment restarts the clock (--head, --note); --spent records spend, which is not
            progress; --stall counts a stall
  pass      `DW-<n> --evidence "…" --by verifier`, on a merged row only. The last DW moves the row to verified
  tick      the orchestrator's input, never a write: ready and assignable tickets, stalled workers (assigned and claimed
            only), READY lines with no PR yet, budget state, watches due, open decisions, STOPs and HOLDs (--json)
  render    prints the `## Swarm` section and writes `sessions/<name>/prs.tsv` (repo, pr, head7, state, ticket; the state
            is the row's status; the header line alone means no open PR)
  resume    compares the ledger with `git ls-remote` and `gh pr list`, prints the difference and what to do with a lost
            worker (drive-to-merge, respawn-in-place, reassign, record-merge), writes nothing without --apply. It never
            writes a pass or a READY head, so a restored row still needs its READY and its verifier (--json)
  grant, watch, decision, hold, budget   the epic's other records; grant and budget need a --decision line
  show      prints the ledger, or one row, as JSON

The file is `<content root>/state/swarm/<key>.json`. Times are UTC, `YYYY-MM-DDTHH:MM:SSZ`; ticket keys are strings:

  {"schema": 1, "key", "title", "repo", "session", "doc", "created", "updated",
   "plan_approved": bool, "plan_decision": <line>, "max_workers": n|null,
   "budget": {"epic_usd": n|null, "worker_usd": n|null}, "spent": n, "waves": [<wave>, …],
   "grants":    [{"id", "scope", "granted_by", "expires", "decision", "at"}],
   "watches":   [{"id", "due", "owner", "what", "closed"}],
   "decisions": [{"id", "question", "ticket", "opened", "closed", "line"}],
   "holds":     [{"scope", "releaser", "reason", "at", "released_at", "released_by"}],
   "seen":      {<line id>: <the line>},
   "log":       [{"at", "act", "ticket", "from", "to", "by", "note", …}],
   "tickets":   {<ticket>: {
      "ticket", "title", "repo", "area": [path, …], "deps": [ticket, …], "sizing", "wave", "incident": bool,
      "status": ready|assigned|claimed|pr|ready-to-merge|merged|verified|done|blocked|parked,
      "worker": {"kind": subagent|peer|headless|null, "name", "ref", "worktree", "branch"},
      "head": <the last known pushed sha>, "ready_head": <the sha of the READY in force, the one the gates pin>,
      "body": <PR body file>, "pr": n|null, "pr_state": open|merged|closed|null, "merged_sha", "merged_by",
      "budget_cap": n|null, "spent": n, "last_progress", "stalls": n, "stall_counted_at", "rebriefs": n,
      "dws": [DW-n, …], "passes": {DW-n: {"evidence", "by", "at"}},
      "blocked": {"reason", "who", "sha", "ran", "at"}|null, "parked_reason",
      "lines": {<sender>: {<WORD>: {"id", "at", …the line's fields}}}, "restored": true when `resume --apply` wrote it}}}

Another script reads the file as plain JSON or imports this module: `load(key)`, `edit(key)` (a locked read-modify-write),
`ledger_path(key)`, `tick_report(led)`, `budget_state(led)`, `worker_limit(led)` (the last two resolve ledger, then config,
then default), `next_status(act, status)`, `parse_status_line(line)`, `STATUSES`, `MOVES`. Unknown keys in a row or in the file are kept. `edit` writes only when something changed.

Config, defaults here: `swarm.stall_minutes` 90, `swarm.max_workers` 4, `swarm.budget.epic_usd` and
`swarm.budget.worker_usd` none (no cap until `init` or `budget` sets one).

Exit codes: 0 ok; 1 no such ledger or ticket; 2 bad usage, a malformed line, a failed read; 3 refused by a rule. Stdlib only.
"""
from __future__ import annotations
import argparse
import collections
import contextlib
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import done_when  # noqa: E402 — the one `## Done when` parser
import fsutil  # noqa: E402 — the atomic write, the lock and the `<file | ->` reader
import kit_profile as profile  # noqa: E402 — the one content-root resolver and config reader

SCHEMA = 1
STATUSES = ("ready", "assigned", "claimed", "pr", "ready-to-merge", "merged", "verified", "done", "blocked", "parked")
# `blocked` and `parked` branch off before `merged`. A blocked row can still be parked. A parked row is not in this set:
# only a new ASSIGN restarts it (docs/swarm.md § The lines, `PARKED`), so a late BLOCKED leaves it parked.
BEFORE_MERGED = ("ready", "assigned", "claimed", "pr", "ready-to-merge", "blocked")
STALL_STATUSES = ("assigned", "claimed")
NO_WORKER_STATUSES = ("ready", "merged", "verified", "done", "parked")
STALLS_BEFORE_REBRIEF = 2

Move = collections.namedtuple("Move", "act frm to when otherwise", defaults=(None, None))
# docs/swarm.md § What moves a row, one entry per table row, in table order; the first match wins. `to=None` is "no move".
# `when` names a condition: when it is false the entry yields `otherwise`, or the walk goes on to the next entry.
MOVES = (
    Move("assign", ("ready", "assigned", "claimed", "blocked", "parked"), "assigned"),
    Move("rebrief", STATUSES, None),
    Move("CLAIMED", ("assigned",), "claimed"),
    Move("PR", ("claimed",), "pr"),
    Move("READY", ("ready-to-merge",), "pr", "new_head"),
    Move("READY", ("blocked",), "pr", "pr_open", "claimed"),
    Move("READY", STATUSES, None),
    Move("gates", ("pr",), "ready-to-merge"),
    Move("BLOCKED", BEFORE_MERGED, "blocked"),
    Move("PARKED", BEFORE_MERGED, "parked"),
    Move("MERGED", ("ready-to-merge",), "merged"),
    Move("pass", ("merged",), "verified"),
    Move("fail", ("merged",), "blocked"),
    Move("close", ("verified",), "done"),
)
# The paragraph under that table: a merge the owner made or an auto-merge caused can reach a row at `pr`.
MERGED_OBSERVED_FROM = ("pr",)
MERGED_BY = ("orchestrator", "owner", "auto")

# docs/swarm.md § The lines: the four lines `apply` takes, each with its positional fields after the ticket.
STATUS_LINES = {"CLAIMED": ("branch",), "PR": ("branch", "sha"), "READY": ("sha",), "BLOCKED": ("reason", "who")}
BLOCKED_REASONS = ("budget", "stop", "lease", "scope", "input")
GRAMMAR_VERSIONS = ("1",)
LINE_KEYS = ("body", "sha", "ran", "note")

WORKER_KINDS = ("subagent", "peer", "headless")
PLAN_FIELDS = ("area", "deps", "wave", "sizing", "budget_cap")  # a change here is a re-plan; a title or a new DW id is not
DEFAULTS = {"swarm.stall_minutes": 90, "swarm.max_workers": 4, "swarm.budget.epic_usd": None, "swarm.budget.worker_usd": None}
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA_RE = re.compile(r"[0-9a-f]{7,64}")
DW_RE = re.compile(r"DW-\d+")
CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)\b", re.I)
LEDGER_TIME = "%Y-%m-%dT%H:%M:%SZ"
PR_LIMIT = 300
PRS_HEADER = "repo\tpr\thead7\tstate\tticket"
RENDER_NOTE = "<!-- Rendered by swarm.py render from the task ledger. Change the ledger, not this table. -->"


class SwarmError(Exception):
    """Bad usage, a malformed line or a failed read: exit 2."""
    code = 2


class Absent(SwarmError):
    """No such ledger or ticket: exit 1."""
    code = 1


class Refused(SwarmError):
    """A rule of the contract says no: exit 3."""
    code = 3


# ── the state machine ───────────────────────────────────────────────────────────────────────
def next_status(act: str, status: str, *, new_head: bool = False, pr_open: bool = False) -> str | None:
    """Where `act` takes a row at `status`: a status (the row's own when the table says "no move"), or None when the
    table has no such edge."""
    flags = {"new_head": new_head, "pr_open": pr_open}
    for m in MOVES:
        if m.act != act or status not in m.frm:
            continue
        if m.when and not flags[m.when]:
            if m.otherwise is None:
                continue
            return m.otherwise
        return status if m.to is None else m.to
    return None


def same_sha(a: str | None, b: str | None) -> bool:
    """One head, whichever of the two is the short form."""
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


def pr_open(row: dict) -> bool:
    return row.get("pr") is not None and row.get("pr_state") == "open"


# ── the file ────────────────────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime(LEDGER_TIME)


def parse_time(text: str) -> datetime:
    """An ISO 8601 time; a time with no zone is UTC. `fromisoformat` takes no `Z` before Python 3.11."""
    try:
        t = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise SwarmError(f"not an ISO 8601 time: {text!r}") from e
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def utc_iso(text: str) -> str:
    return parse_time(text).astimezone(timezone.utc).strftime(LEDGER_TIME)


def check_name(value: str, what: str) -> str:
    if not NAME_RE.fullmatch(value or ""):
        raise SwarmError(f"bad {what}: {value!r} (letters, digits, dot, dash, underscore)")
    return value


def clean_ticket(value: str) -> str:
    return check_name(str(value).lstrip("#"), "ticket")


def check_sha(value: str) -> str:
    if not SHA_RE.fullmatch(value or ""):
        raise SwarmError(f"bad sha: {value!r} (7 to 64 lowercase hex digits)")
    return value


def state_dir() -> Path:
    return profile.context_root() / "state" / "swarm"


def ledger_path(key: str) -> Path:
    return state_dir() / f"{check_name(key, 'epic key')}.json"


def dumps(led: dict) -> str:
    return json.dumps(led, indent=2, ensure_ascii=False) + "\n"


def load(key: str) -> dict:
    path = ledger_path(key)
    try:
        led = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Absent(f"no ledger for {key} at {path}; run `swarm.py init {key}`") from None
    except (ValueError, OSError) as e:
        raise SwarmError(f"{path}: cannot read: {e}") from e
    if not isinstance(led, dict) or led.get("schema") != SCHEMA:
        raise SwarmError(f"{path}: not a schema {SCHEMA} ledger")
    return led


@contextlib.contextmanager
def edit(key: str):
    """A locked read-modify-write of the ledger. It writes only when something changed, and nothing when the body raises."""
    path = ledger_path(key)
    if not path.is_file():
        load(key)  # the one absent-ledger message
    with fsutil.locked(str(path)):
        led = load(key)
        before = dumps(led)
        yield led
        if dumps(led) != before:
            led["updated"] = now_iso()
            fsutil.atomic_write(str(path), dumps(led))


def log(led: dict, act: str, ticket: str | None = None, **fields) -> None:
    entry = {"at": now_iso(), "act": act}
    if ticket is not None:
        entry["ticket"] = ticket
    entry.update({k: v for k, v in fields.items() if v is not None})
    led["log"].append(entry)


def new_row(ticket: str) -> dict:
    return {"ticket": ticket, "title": None, "repo": None, "area": [], "deps": [], "sizing": None, "wave": None,
            "incident": False, "status": "ready",
            "worker": {"kind": None, "name": None, "ref": None, "worktree": None, "branch": None},
            "head": None, "ready_head": None, "body": None, "pr": None, "pr_state": None, "merged_sha": None, "merged_by": None,
            "budget_cap": None, "spent": 0, "last_progress": None, "stalls": 0, "stall_counted_at": None, "rebriefs": 0,
            "dws": [], "passes": {}, "blocked": None, "parked_reason": None, "lines": {}}


def row_of(led: dict, ticket: str) -> dict:
    row = led["tickets"].get(ticket)
    if row is None:
        raise Absent(f"{led['key']}: no row for ticket {ticket}")
    return row


def set_status(led: dict, ticket: str, row: dict, to: str, act: str, **fields) -> None:
    log(led, act, ticket, **{"from": row["status"], "to": to}, **fields)
    row["status"] = to


def config(key: str):
    """A numeric `swarm.*` key, or its default in `DEFAULTS` when the store has none or holds something else."""
    value = profile.get(key, DEFAULTS[key])
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0):
        return value
    print(f"swarm: {key} is not a number ({value!r}); using {DEFAULTS[key]}", file=sys.stderr)
    return DEFAULTS[key]


# ── what the tick reads ─────────────────────────────────────────────────────────────────────
def read_stops() -> list[str]:
    """The scopes of the STOP files in force (`.context/state/swarm/STOP-<scope>`, written by `lease.py stop`)."""
    d = state_dir()
    return sorted(p.name[len("STOP-"):] for p in d.glob("STOP-*") if p.suffix != ".lock") if d.is_dir() else []


def live_holds(led: dict) -> list[dict]:
    return [h for h in led["holds"] if not h.get("released_at")]


def row_held(led: dict, row: dict) -> bool:
    scopes = {h["scope"] for h in live_holds(led)}
    return bool(scopes & {row["ticket"], row["worker"]["name"], led["key"]})


def unmet_deps(led: dict, row: dict) -> list[str]:
    rows = led["tickets"]
    return [d for d in row["deps"] if d not in rows or rows[d]["status"] != "done"]


def current_wave(led: dict) -> str | None:
    """The first wave with work that can still move. A blocked or parked row and a row waiting behind one do not hold
    the wave: the work behind a stop waits and everything else goes on (docs/swarm.md § The swarm tick)."""
    rows = led["tickets"]

    def stuck(ticket: str, seen: frozenset = frozenset()) -> bool:
        row = rows.get(ticket)
        if row is None or ticket in seen or row["status"] in ("blocked", "parked"):
            return True
        return row["status"] == "ready" and any(stuck(d, seen | {ticket}) for d in row["deps"]
                                                if d not in rows or rows[d]["status"] != "done")

    for wave in led["waves"]:
        if any(r["wave"] == wave and r["status"] != "done" and not stuck(t) for t, r in rows.items()):
            return wave
    return None


def active_tickets(led: dict) -> list[str]:
    """The rows whose worker is running. A READY in force means the run ended and the next act is the orchestrator's."""
    return [t for t, r in led["tickets"].items() if r["status"] in STALL_STATUSES and not r["ready_head"]]


def row_cap(led: dict, row: dict):
    if row["budget_cap"] is not None:
        return row["budget_cap"]
    worker = led["budget"].get("worker_usd")
    return worker if worker is not None else config("swarm.budget.worker_usd")


def budget_state(led: dict) -> dict:
    """`state` is `over` at or past the epic cap, `no-cap` with none set, else `ok`; `over` lists the rows at their own cap."""
    epic = led["budget"].get("epic_usd")
    epic = epic if epic is not None else config("swarm.budget.epic_usd")
    rows = led["tickets"]
    spent = max(led.get("spent") or 0, sum(r.get("spent") or 0 for r in rows.values()))
    over = []
    for t, r in rows.items():
        cap = row_cap(led, r)
        if cap is not None and (r.get("spent") or 0) >= cap and r["status"] not in NO_WORKER_STATUSES:
            over.append({"ticket": t, "spent": r.get("spent") or 0, "cap": cap})
    state = "no-cap" if epic is None else ("over" if spent >= epic else "ok")
    return {"state": state, "epic_usd": epic, "spent": spent, "over": over}


def hard_reasons(led: dict) -> list[str]:
    """Why nothing may spawn, an incident lane included: an incident widens no cap or permission."""
    out = []
    if led["key"] in read_stops():
        out.append(f"STOP on {led['key']}")
    if any(h["scope"] == led["key"] for h in live_holds(led)):
        out.append(f"HOLD on {led['key']}")
    b = budget_state(led)
    if b["state"] == "over":
        out.append(f"epic budget at its cap ({b['spent']:.2f} of {b['epic_usd']:.2f})")
    return out


def row_reasons(led: dict, row: dict) -> list[str]:
    out = []
    scopes = {row["ticket"], row["worker"]["name"]} & set(read_stops())
    if scopes:
        out.append(f"STOP on {', '.join(sorted(scopes))}")
    if row_held(led, row):
        out.append(f"HOLD on {row['ticket']}")
    cap = row_cap(led, row)
    if cap is not None and (row.get("spent") or 0) >= cap:
        out.append(f"worker budget at its cap ({row.get('spent') or 0:.2f} of {cap:.2f})")
    return out


def worker_limit(led: dict) -> int:
    limit = led.get("max_workers")
    return int(limit if limit is not None else config("swarm.max_workers"))


def stall_step(row: dict) -> str:
    """What the next stall means: count it, re-brief at the second, then reassign or park (§ The swarm tick, step 5)."""
    if row["stalls"] + 1 < STALLS_BEFORE_REBRIEF:
        return "count"
    return "rebrief" if not row["rebriefs"] else "reassign-or-park"


def tick_report(led: dict, now: datetime | None = None) -> dict:
    """The orchestrator's input at a tick. Reads the ledger and the STOP files; writes nothing."""
    moment = now or datetime.now(timezone.utc)
    rows = led["tickets"]
    limit = config("swarm.stall_minutes")
    wave = current_wave(led)
    ready, waiting = [], []
    for t, r in rows.items():
        if r["status"] == "ready":
            unmet = unmet_deps(led, r)
            if unmet and not r["incident"]:
                waiting.append({"ticket": t, "on": unmet})
            else:
                ready.append(t)
    approved = bool(led["plan_approved"])
    queue = [t for t in ready if rows[t]["incident"]]
    queue += [t for t in ready if not rows[t]["incident"] and approved and rows[t]["wave"] in (None, wave)]
    queue = [t for t in queue if not row_reasons(led, rows[t])]
    active = active_tickets(led)
    slots = max(0, worker_limit(led) - len(active))
    hard = hard_reasons(led)
    no_assign = list(hard) + ([] if approved else ["plan not approved"]) + ([] if slots else ["no free worker slot"])
    stalled, integrate, needs_ready = [], [], []
    for t, r in rows.items():
        if r["status"] in STALL_STATUSES and not r["ready_head"] and not row_held(led, r):
            since = max(parse_time(x) for x in (r["last_progress"] or led["created"], r["stall_counted_at"] or led["created"]))
            minutes = (moment - since).total_seconds() / 60
            if minutes > limit:
                stalled.append({"ticket": t, "worker": r["worker"]["name"], "minutes": int(minutes), "stalls": r["stalls"],
                                "next": stall_step(r)})
        if (r["status"] in STALL_STATUSES and r["ready_head"]) or (r["status"] == "pr" and r["pr"] is None):
            integrate.append({"ticket": t, "status": r["status"], "head": r["head"], "body": r["body"], "pr": r["pr"]})
        if r["status"] in ("pr", "ready-to-merge") and r["ready_head"] and not same_sha(r["head"], r["ready_head"]):
            needs_ready.append({"ticket": t, "head": r["head"], "ready_head": r["ready_head"]})
    return {"key": led["key"], "now": moment.astimezone(timezone.utc).strftime(LEDGER_TIME), "plan_approved": approved,
            "wave": wave, "workers": len(active), "max_workers": worker_limit(led), "stall_minutes": limit,
            "ready": ready, "waiting": waiting, "assignable": [] if hard else queue[:slots], "no_assign": no_assign,
            "stalled": stalled, "integrate": integrate, "needs_ready": needs_ready, "budget": budget_state(led),
            "watches_due": [w for w in led["watches"] if not w.get("closed") and parse_time(w["due"]) <= moment],
            "decisions_open": [d for d in led["decisions"] if not d.get("closed")],
            "grants_expired": [g for g in led["grants"] if g.get("expires") and parse_time(g["expires"]) <= moment],
            "holds": live_holds(led), "stops": read_stops()}


# ── render ──────────────────────────────────────────────────────────────────────────────────
def cell(value) -> str:
    text = "-" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def ticket_ref(ticket: str) -> str:
    return f"#{ticket}" if ticket.isdigit() else ticket


def render_table(led: dict) -> str:
    b = budget_state(led)
    cap = "no cap" if b["epic_usd"] is None else f"of ${b['epic_usd']:.2f}"
    summary = (f"Epic `{led['key']}`: plan {'approved' if led['plan_approved'] else 'not approved'}"
               f" · wave {current_wave(led) or '-'} · spent ${b['spent']:.2f} {cap}"
               f" · open decisions {sum(not d.get('closed') for d in led['decisions'])}"
               f" · open watches {sum(not w.get('closed') for w in led['watches'])}")
    out = ["## Swarm", "", RENDER_NOTE, "", summary, "",
           "| Ticket | Status | Wave | Worker | Head | PR | Spent / cap | Last progress | Stalls | Passes | Deps |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for t, r in led["tickets"].items():
        status = r["status"]
        if status == "blocked" and r.get("blocked"):
            status = f"blocked ({r['blocked'].get('reason')}, {r['blocked'].get('who')})"
        w = r["worker"]
        worker = f"{w['name']} ({w['kind']})" if w.get("name") else None
        cap_ = row_cap(led, r)
        spend = f"${r.get('spent') or 0:.2f} / " + ("-" if cap_ is None else f"${cap_:.2f}")
        passes = f"{len(r['passes'])}/{len(r['dws'])}" if r["dws"] else None
        last = profile.local_str(r["last_progress"]) if r.get("last_progress") else None
        cols = (ticket_ref(t), status, r["wave"], worker, (r["head"] or "")[:7], f"#{r['pr']}" if r["pr"] else None, spend, last,
                r["stalls"], passes, ", ".join(ticket_ref(d) for d in r["deps"]))
        out.append("| " + " | ".join(cell(c) for c in cols) + " |")
    return "\n".join(out) + "\n"


def prs_tsv(led: dict) -> str:
    """The machine-readable open-PR table a successor reads (docs/swarm.md § The swarm tick, step 10)."""
    out = [PRS_HEADER]
    for t, r in led["tickets"].items():
        if pr_open(r):
            out.append("\t".join((r["repo"] or led["repo"] or "", str(r["pr"]), (r["head"] or "")[:7], r["status"], t)))
    return "\n".join(out) + "\n"


# ── apply ───────────────────────────────────────────────────────────────────────────────────
def parse_status_line(line: str) -> dict:
    """One status line of docs/swarm.md § Message grammar, as a dict: `word`, `ticket`, the line's fields, `v`, `id`, `from`.
    Takes the four status lines only; anything else raises `SwarmError`."""
    try:
        parts = shlex.split(line)
    except ValueError as e:
        raise SwarmError(f"not a grammar line ({e}): {line!r}") from e
    word = parts[0] if parts else ""
    if word not in STATUS_LINES:
        raise SwarmError(f"apply takes {', '.join(STATUS_LINES)}, not {word or 'an empty line'!r}: DONE moves no row, and "
                         "the orchestrator writes the other moves with assign and status")
    positional, keyed = [], {}
    for part in parts[1:]:
        m = re.fullmatch(r"([a-z_]+)=(.*)", part, re.S)
        if m:
            keyed[m.group(1)] = m.group(2)
        elif keyed:
            raise SwarmError(f"a field after a key=value pair: {line!r}")
        else:
            positional.append(part)
    names = ("ticket", *STATUS_LINES[word])
    if len(positional) != len(names):
        raise SwarmError(f"{word} takes {' '.join('<' + n + '>' for n in names)}: {line!r}")
    for need in ("v", "id", "from"):
        if not keyed.get(need):
            raise SwarmError(f"no {need}= on the line: {line!r}")
    if keyed["v"] not in GRAMMAR_VERSIONS:
        raise SwarmError(f"grammar version {keyed['v']} on the line; this script reads {', '.join(GRAMMAR_VERSIONS)}: {line!r}")
    rec = {"word": word, **dict(zip(names, positional)), "v": keyed["v"], "id": keyed["id"], "from": keyed["from"]}
    rec["ticket"] = clean_ticket(rec["ticket"])
    rec.update({k: keyed[k] for k in LINE_KEYS if k in keyed})
    if word == "PR" and not rec.get("body"):
        raise SwarmError(f"no body= on the PR line: {line!r}")
    if "sha" in rec:
        check_sha(rec["sha"])
    if word == "BLOCKED" and rec["reason"] not in BLOCKED_REASONS:
        raise SwarmError(f"BLOCKED reason must be one of {', '.join(BLOCKED_REASONS)}: {line!r}")
    return rec


def apply_line(led: dict, line: str) -> str:
    """One status line onto the ledger: § What every line obeys and § What moves a row. Returns what it did, in one line."""
    rec = parse_status_line(line)
    text = line.strip()
    if rec["id"] in led["seen"]:
        same = led["seen"][rec["id"]] == text
        return f"no change: id {rec['id']} is already applied" + ("" if same else ", and that copy read differently")
    ticket, word, sender = rec["ticket"], rec["word"], rec["from"]
    row = row_of(led, ticket)
    led["seen"][rec["id"]] = text
    now = now_iso()
    # Latest wins per sender, scope and line kind: kinds do not replace each other, and neither do senders.
    fields = {k: v for k, v in rec.items() if k not in ("word", "ticket", "from", "v")}
    row["lines"].setdefault(sender, {})[word] = {**fields, "at": now}
    worker = row["worker"]["name"]
    if worker and sender != worker:
        log(led, word, ticket, by=sender, id=rec["id"], note=f"sender is not the row's worker {worker}")
        return f"{word} {ticket}: no move, sent by {sender} and the row's worker is {worker}"
    touch(row, now)
    before = row["status"]
    sha = rec.get("sha")
    to = next_status(word, before, new_head=word == "READY" and not same_sha(sha, row["ready_head"]), pr_open=pr_open(row))
    if to is None:
        log(led, word, ticket, by=sender, id=rec["id"], note=f"no edge from {before}")
        return f"{word} {ticket}: no move from {before}"
    if word == "CLAIMED":
        row["worker"]["branch"] = rec["branch"]
    elif word == "PR":
        row["worker"]["branch"], row["head"], row["body"] = rec["branch"], sha, rec.get("body")
    elif word == "READY":
        row["head"], row["ready_head"], row["blocked"] = sha, sha, None
    elif word == "BLOCKED":
        # A BLOCKED newer than the READY takes the ticket out of the merge gate until a newer READY comes.
        row["blocked"] = {"reason": rec["reason"], "who": rec["who"], "sha": sha, "ran": rec.get("ran"), "at": now}
        row["ready_head"] = None
        row["head"] = sha or row["head"]
    if to == before:
        log(led, word, ticket, by=sender, id=rec["id"], sha=sha)
        return f"{word} {ticket}: no move, {before}" + (f", head {sha[:7]} recorded" if word == "READY" else "")
    set_status(led, ticket, row, to, word, by=sender, id=rec["id"], sha=sha)
    return f"{word} {ticket}: {before} -> {to}"


def touch(row: dict, now: str) -> None:
    """A new commit, status or comment: the progress clock restarts and the stall count clears."""
    row["last_progress"] = now
    row["stalls"] = 0
    row["stall_counted_at"] = None


# ── resume ──────────────────────────────────────────────────────────────────────────────────
def read_cmd(argv: list[str], cwd: str | None, what: str, env: dict | None = None) -> str:
    """A command's stdout. A failed read stops the run: it never reads as "nothing on the remote"."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env,
                           stdin=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SwarmError(f"{what} failed: {e}") from e
    if p.returncode:
        tail = (p.stderr.strip().splitlines() or [f"exit {p.returncode}"])[-1]
        raise SwarmError(f"{what} failed: {tail}")
    return p.stdout


def read_heads(repo_dir: str, remote: str) -> dict[str, str]:
    out = read_cmd(["git", "-C", repo_dir, "ls-remote", "--heads", remote], None, "git ls-remote")
    heads = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return heads


def read_prs(repo: str | None, repo_dir: str) -> list[dict]:
    argv = ["gh", "pr", "list", "--state", "all", "--limit", str(PR_LIMIT),
            "--json", "headRefName,headRefOid,number,body,state,mergeCommit"]
    argv += ["-R", repo] if repo else []
    out = read_cmd(argv, repo_dir, "gh pr list", env=profile.gh_env())
    try:
        prs = json.loads(out or "[]")
    except ValueError as e:
        raise SwarmError(f"gh pr list: not JSON: {e}") from e
    if len(prs) >= PR_LIMIT:
        print(f"swarm: gh pr list stopped at {PR_LIMIT}: an older PR is not read", file=sys.stderr)
    return prs


def read_worktrees(repo_dir: str) -> dict[str, str]:
    """branch -> worktree path. No worktree list is not an error: a remote can be read from a plain clone."""
    try:
        out = read_cmd(["git", "-C", repo_dir, "worktree", "list", "--porcelain"], None, "git worktree list")
    except SwarmError:
        return {}
    trees, path = {}, None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and path:
            trees[line[len("branch refs/heads/"):]] = path
    return trees


def dirty_count(worktree: str) -> int | None:
    try:
        return len(read_cmd(["git", "-C", worktree, "status", "--porcelain"], None, "git status").splitlines())
    except SwarmError:
        return None


def ticket_branch_re(ticket: str):
    """The brief's branch convention, `<type>/<ticket>-<slug>`: the ticket key as a whole path segment prefix."""
    return re.compile(rf"(?:^|/){re.escape(ticket)}(?:$|[-_/.])")


def compare_row(ticket: str, row: dict, heads: dict, prs: list, trees: dict) -> dict:
    """What the remote says about one row: the fields that differ and what to do if its worker is lost."""
    status, known = row["status"], row["worker"]["branch"]
    if known:  # a row that names its branch trusts only that branch, so a merged PR of an earlier assignment never counts
        candidates = [p for p in prs if p.get("headRefName") == known]
        branches = [known] if known in heads else []
    else:
        pat = ticket_branch_re(ticket)
        candidates = [p for p in prs if pat.search(p.get("headRefName") or "")
                      or ticket in CLOSES_RE.findall(p.get("body") or "")]
        branches = sorted(b for b in heads if pat.search(b))
    open_prs = [p for p in candidates if p.get("state") == "OPEN"]
    merged_prs = sorted((p for p in candidates if p.get("state") == "MERGED"), key=lambda p: p.get("number") or 0)
    result = {"ticket": ticket, "status": status, "changes": {}, "action": "none", "branch": known, "worktree": None,
              "dirty": None, "pr": row["pr"], "note": None}
    if status in ("verified", "done"):
        return result
    if len(open_prs) > 1 or (not open_prs and not merged_prs and len(branches) > 1):
        names = [f"#{p.get('number')}" for p in open_prs] if len(open_prs) > 1 else branches
        result.update(action="ask", note=f"more than one candidate: {', '.join(names)}")
        return result
    pr = open_prs[0] if open_prs else (merged_prs[-1] if merged_prs else None)
    branch = (pr or {}).get("headRefName") or (branches[0] if branches else known)
    head = (pr or {}).get("headRefOid") or heads.get(branch)
    worktree = row["worker"]["worktree"] if row["worker"]["worktree"] and Path(row["worker"]["worktree"]).is_dir() else trees.get(branch)
    changes = result["changes"]

    def change(field: str, old, new) -> None:
        if new is not None and new != old:
            changes[field] = [old, new]

    change("worker.branch", known, branch)
    change("worker.worktree", row["worker"]["worktree"], worktree)
    if head and not same_sha(row["head"], head):
        change("head", row["head"], head)
    state = pr.get("state") if pr else None
    if pr:
        change("pr", row["pr"], pr.get("number"))
        change("pr_state", row["pr_state"], state.lower())
    if state == "MERGED" and not row["merged_sha"]:
        change("merged_sha", None, (pr.get("mergeCommit") or {}).get("oid"))
        if status not in ("merged", "blocked", "parked"):
            change("status", status, "merged")
    elif state == "OPEN" and status in ("ready", "assigned", "claimed"):
        change("status", status, "pr")
    elif not pr and branch in heads and status in ("ready", "assigned"):
        change("status", status, "claimed")
    result.update(branch=branch, worktree=worktree, dirty=dirty_count(worktree) if worktree else None,
                  pr=(pr or {}).get("number", row["pr"]))
    if status in ("ready", "assigned", "claimed"):
        if state == "OPEN":
            result["action"] = "drive-to-merge"
        elif state == "MERGED":
            result["action"] = "record-merge"
        elif worktree:
            result["action"] = "respawn-in-place"
        elif status != "ready" or branch in heads:
            result["action"] = "reassign"
    elif row["pr"] and not pr and status in ("pr", "ready-to-merge"):
        result.update(action="ask", note=f"the ledger names PR #{row['pr']} and gh does not list it")
    elif changes:
        result["action"] = "update"
    return result


def restore_row(led: dict, ticket: str, row: dict, changes: dict) -> None:
    """`resume --apply`: the row as the remote shows it. A restore is not a move, and it never writes a pass or a READY head."""
    before = {f: old for f, (old, _) in changes.items()}
    for field, (_, new) in changes.items():
        if field.startswith("worker."):
            row["worker"][field[len("worker."):]] = new
        else:
            row[field] = new
    row["last_progress"] = now_iso()
    row["restored"] = True
    log(led, "resume", ticket, note=json.dumps(before, ensure_ascii=False))


# ── subcommands ─────────────────────────────────────────────────────────────────────────────
def cmd_init(a) -> int:
    path = ledger_path(a.key)
    now = now_iso()
    led = {"schema": SCHEMA, "key": a.key, "title": a.title, "repo": a.repo,
           "session": check_name(a.session, "session name") if a.session else None, "doc": a.doc,
           "created": now, "updated": now, "plan_approved": False, "plan_decision": None, "max_workers": a.max_workers,
           "budget": {"epic_usd": a.epic_usd, "worker_usd": a.worker_usd}, "spent": 0,
           "waves": [w for w in (a.waves or "").split(",") if w], "grants": [], "watches": [], "decisions": [], "holds": [],
           "seen": {}, "log": [], "tickets": {}}
    with fsutil.locked(str(path)):
        if path.exists():
            raise Refused(f"{a.key}: a ledger already exists at {path}")
        fsutil.atomic_write(str(path), dumps(led))
    print(path)
    return 0


def cmd_add(a) -> int:
    ticket = clean_ticket(a.ticket)
    deps = [clean_ticket(d) for d in a.dep] if a.dep is not None else None
    if deps and ticket in deps:
        raise SwarmError(f"{ticket} cannot depend on itself")
    dws = list(a.dw or [])
    for dw in dws:
        if not DW_RE.fullmatch(dw):
            raise SwarmError(f"bad id: {dw!r} (DW-<n>)")
    if a.ticket_file:
        crits, problems = done_when.parse_ticket(fsutil.read_arg(a.ticket_file))
        if problems or not crits:
            raise SwarmError(f"{a.ticket_file}: {problems[0] if problems else 'no DW-<n> id in its ## Done when'}")
        dws += [c.id for c in crits]
    with edit(a.key) as led:
        old = led["tickets"].get(ticket)
        if old is not None and not a.update:
            raise Refused(f"{ticket} already has a row; --update changes it")
        if old is not None and a.already_done:
            raise Refused(f"{ticket} already has a row; --already-done is for a new one")
        row = json.loads(json.dumps(old)) if old is not None else new_row(ticket)
        for field, value in (("title", a.title), ("repo", a.repo), ("area", a.area), ("deps", deps), ("sizing", a.sizing),
                             ("wave", a.wave), ("budget_cap", a.cap_usd)):
            if value is not None:
                row[field] = value
        row["dws"] = list(dict.fromkeys([*row["dws"], *dws]))  # ids are stable for a ticket's life: a delta only adds
        row["incident"] = row["incident"] or a.incident
        if a.already_done:
            row["status"] = "done"
        if row == old:
            print(f"{ticket}: no change")
            return 0
        led["tickets"][ticket] = row
        if row["wave"] and row["wave"] not in led["waves"]:
            led["waves"].append(row["wave"])
        log(led, "add", ticket, to=row["status"])
        replanned = old is None or any(row[f] != old[f] for f in PLAN_FIELDS)
        if led["plan_approved"] and replanned and not (a.incident or a.already_done):
            led["plan_approved"] = False
            log(led, "replan", ticket, note="a row changed on an approved plan: the plan needs a new go")
            print(f"{ticket}: the plan needs a new go", file=sys.stderr)
    print(f"{ticket}: {row['status']}")
    return 0


def cmd_approve(a) -> int:
    if not a.decision.strip():
        raise Refused("a plan's go needs its decision line")
    with edit(a.key) as led:
        led["plan_approved"], led["plan_decision"] = True, a.decision.strip()
        log(led, "approve", note=a.decision.strip())
    print(f"{a.key}: plan approved")
    return 0


def rebrief(led: dict, ticket: str, row: dict) -> str:
    """`ASSIGN … rebrief=1`: no row moves. The stall window restarts, or the next tick would flag the row again at once."""
    row["rebriefs"] += 1
    row["stall_counted_at"] = now_iso()
    log(led, "rebrief", ticket, to=next_status("rebrief", row["status"]))
    return f"{ticket}: re-brief {row['rebriefs']}, no move, {row['status']}"


def cmd_assign(a) -> int:
    ticket = clean_ticket(a.ticket)
    with edit(a.key) as led:
        row = row_of(led, ticket)
        if a.rebrief:
            print(rebrief(led, ticket, row))
            return 0
        if not a.kind or not a.name:
            raise SwarmError("assign needs --kind and --name, or --rebrief")
        check_name(a.name, "worker name")
        before = row["status"]
        to = next_status("assign", before)
        if to is None:
            raise Refused(f"{ticket}: no assignment from {before}; the table moves {', '.join(MOVES[0].frm)}")
        reasons = hard_reasons(led) + row_reasons(led, row)
        if not row["incident"]:
            reasons += [] if led["plan_approved"] else ["plan not approved"]
            unmet = unmet_deps(led, row)
            reasons += [f"waiting on {', '.join(unmet)}"] if unmet else []
        if len([t for t in active_tickets(led) if t != ticket]) >= worker_limit(led):
            reasons.append(f"no free worker slot ({worker_limit(led)} running)")
        failed_merge = before == "blocked" and row["merged_sha"]
        branch = a.branch or row["worker"]["branch"]
        if failed_merge and (not a.branch or a.branch == row["worker"]["branch"]):
            reasons.append("a fix for a FAIL on the merged commit needs a new --branch")
        if reasons:
            raise Refused(f"{ticket}: not assigned: {'; '.join(reasons)}")
        if branch != row["worker"]["branch"] and row["worker"]["branch"]:  # a head and a PR belong to a branch
            row.update(head=None, ready_head=None, body=None, pr=None, pr_state=None)
        if failed_merge:  # the fix merges again and is verified whole: a pass on the old commit proves nothing about the new
            row.update(merged_sha=None, merged_by=None, passes={})
        if row["worker"]["name"] != a.name:
            row["rebriefs"] = 0
        row["worker"] = {"kind": a.kind, "name": a.name, "ref": a.ref, "worktree": a.worktree, "branch": branch}
        row.update(blocked=None, parked_reason=None)
        if a.cap_usd is not None:
            row["budget_cap"] = a.cap_usd
        touch(row, now_iso())
        set_status(led, ticket, row, to, "assign", by=a.name)
    print(f"{ticket}: {before} -> {to}")
    return 0


def cmd_status(a) -> int:
    ticket = clean_ticket(a.ticket)
    pointers = {"assigned": "`swarm.py assign` writes it", "claimed": "a CLAIMED line writes it, through `swarm.py apply`",
                "verified": "only `swarm.py pass` writes it", "ready": "no line or act moves a row back to ready"}
    if a.status in pointers:
        raise Refused(f"{ticket}: status {a.status} refused: {pointers[a.status]}")
    with edit(a.key) as led:
        row = row_of(led, ticket)
        before = row["status"]
        act, extra = status_act(a, row, ticket)
        if act == "MERGED" and before in MERGED_OBSERVED_FROM and a.merged_by in ("owner", "auto"):
            to = "merged"
        else:
            to = next_status(act, before)
        if before == a.status:
            to = before
        if to != a.status:
            raise Refused(f"{ticket}: no move from {before} to {a.status}")
        if to == before:  # a repeat moves nothing: it may correct a PR number or a parked reason, never a record of a move
            if a.status in ("pr", "parked"):
                row.update(extra)
            print(f"{ticket}: no move, {before}")
            return 0
        row.update(extra)
        if a.head and a.status != "ready-to-merge":
            row["head"] = a.head
        set_status(led, ticket, row, to, act, note=a.reason, sha=a.sha)
    print(f"{ticket}: {before} -> {to}")
    return 0


def status_act(a, row: dict, ticket: str) -> tuple[str, dict]:
    """The table's act behind `status <to>`, the checks that act needs, and the fields it writes."""
    if a.head:
        check_sha(a.head)
    if a.sha:
        check_sha(a.sha)
    if a.status == "pr":
        if a.pr is None and row["pr"] is None:
            raise SwarmError(f"{ticket}: status pr needs --pr <number>")
        return "PR", {"pr": a.pr if a.pr is not None else row["pr"], "pr_state": "open"}
    if a.status == "ready-to-merge":
        if not a.head:
            raise SwarmError(f"{ticket}: status ready-to-merge needs --head <the READY head the gates held on>")
        if row["status"] != a.status and not (same_sha(a.head, row["ready_head"]) and same_sha(a.head, row["head"])):
            raise Refused(f"{ticket}: the gates hold at {a.head[:7]}, the READY in force is {(row['ready_head'] or 'none')[:7]} "
                          f"and the head is {(row['head'] or 'none')[:7]}: a moved head needs a new READY")
        if row["pr"] is None:
            raise Refused(f"{ticket}: no PR number on the row; `status pr --pr <n>` first")
        return "gates", {}
    if a.status == "parked":
        if not a.reason:
            raise SwarmError(f"{ticket}: status parked needs --reason")
        return "PARKED", {"parked_reason": a.reason}
    if a.status == "merged":
        if not a.sha:
            raise SwarmError(f"{ticket}: status merged needs --sha <the merged commit>")
        extra = {"merged_sha": a.sha, "merged_by": a.merged_by, "pr_state": "merged"}
        return "MERGED", {**extra, "pr": a.pr} if a.pr is not None else extra
    if a.status == "blocked":
        if not a.reason:
            raise SwarmError(f"{ticket}: status blocked needs --reason (a FAIL on the merged commit; a worker's BLOCKED goes "
                             "through apply)")
        return "fail", {"blocked": {"reason": "verifier-fail", "who": "orchestrator", "sha": row["merged_sha"], "ran": a.reason,
                                    "at": now_iso()}}
    return "close", {}


def cmd_progress(a) -> int:
    ticket = clean_ticket(a.ticket)
    if a.head:
        check_sha(a.head)
    with edit(a.key) as led:
        row = row_of(led, ticket)
        moved = bool(a.head and not same_sha(a.head, row["head"]))
        if a.spent is not None:
            row["spent"] = a.spent
        if a.head:
            row["head"] = a.head
        now = now_iso()
        if a.stall:
            row["stalls"] += 1
            row["stall_counted_at"] = now
            log(led, "stall", ticket, note=a.note)
        elif moved or a.note or (a.head is None and a.spent is None):
            touch(row, now)
            if a.note:
                log(led, "progress", ticket, note=a.note)
        print(f"{ticket}: {row['status']}, stalls {row['stalls']}, spent {row['spent']:.2f}, last progress {row['last_progress']}")
    return 0


def cmd_pass(a) -> int:
    ticket = clean_ticket(a.ticket)
    if a.by != "verifier":
        raise Refused(f"{ticket}: a pass is the verifier's: --by verifier, not --by {a.by}")
    evidence = a.evidence.strip()
    if not evidence:
        raise Refused(f"{ticket}: a pass needs --evidence")
    if not DW_RE.fullmatch(a.dw):
        raise SwarmError(f"bad id: {a.dw!r} (DW-<n>)")
    with edit(a.key) as led:
        row = row_of(led, ticket)
        if a.dw not in row["dws"]:
            raise Refused(f"{ticket}: {a.dw} is not on the row ({', '.join(row['dws']) or 'no DW list'}); `add --update --dw` adds it")
        if row["status"] in ("verified", "done") and a.dw in row["passes"]:
            print(f"{ticket}: no change, {a.dw} is already passed")
            return 0
        if row["status"] != "merged":
            raise Refused(f"{ticket}: a pass follows the merge; the row is at {row['status']}")
        if row["passes"].get(a.dw, {}).get("evidence") == evidence:
            print(f"{ticket}: no change, {a.dw} is already passed")
            return 0
        row["passes"][a.dw] = {"evidence": evidence, "by": a.by, "at": now_iso()}
        log(led, "pass", ticket, by=a.by, note=a.dw)
        missing = [d for d in row["dws"] if d not in row["passes"]]
        if missing:
            print(f"{ticket}: {a.dw} passed, {len(missing)} left: {', '.join(missing)}")
            return 0
        set_status(led, ticket, row, next_status("pass", row["status"]), "pass", by=a.by, note=a.dw)
    print(f"{ticket}: every DW passed, merged -> verified")
    return 0


def cmd_apply(a) -> int:
    parse_status_line(a.line)  # a malformed line is refused before the ledger is touched
    with edit(a.key) as led:
        print(apply_line(led, a.line))
    return 0


def print_tick(rep: dict) -> None:
    b = rep["budget"]
    cap = "none" if b["epic_usd"] is None else f"{b['epic_usd']:.2f}"
    print(f"tick {rep['key']} now={rep['now']} plan={'approved' if rep['plan_approved'] else 'not-approved'} wave={rep['wave'] or '-'} "
          f"workers={rep['workers']}/{rep['max_workers']} budget={b['state']} spent={b['spent']:.2f} cap={cap}")
    for t in rep["ready"]:
        print(f"ready {t}")
    for t in rep["assignable"]:
        print(f"assignable {t}")
    for w in rep["waiting"]:
        print(f"waiting {w['ticket']} on {','.join(w['on'])}")
    for s in rep["stalled"]:
        print(f"stalled {s['ticket']} worker={s['worker']} minutes={s['minutes']} stalls={s['stalls']} next={s['next']}")
    for i in rep["integrate"]:
        print(f"integrate {i['ticket']} status={i['status']} head={(i['head'] or '-')[:7]}")
    for n in rep["needs_ready"]:
        print(f"needs-ready {n['ticket']} head={(n['head'] or '-')[:7]} ready_head={n['ready_head'][:7]}")
    for o in b["over"]:
        print(f"over-budget {o['ticket']} spent={o['spent']:.2f} cap={o['cap']:.2f}")
    for w in rep["watches_due"]:
        print(f"watch-due {w['id']} due={w['due']} owner={w['owner']} what={w['what']}")
    for d in rep["decisions_open"]:
        print(f"decision-open {d['id']} {d['question']}")
    for g in rep["grants_expired"]:
        print(f"grant-expired {g['id']} scope={g['scope']}")
    for h in rep["holds"]:
        print(f"hold {h['scope']} releaser={h['releaser']}")
    for s in rep["stops"]:
        print(f"stop {s}")
    for r in rep["no_assign"]:
        print(f"no-assign {r}")


def cmd_tick(a) -> int:
    rep = tick_report(load(a.key), parse_time(a.now) if a.now else None)
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print_tick(rep)
    return 0


def cmd_render(a) -> int:
    with edit(a.key) as led:
        name = a.session or led.get("session") or profile.session_name()
        if not name:
            raise SwarmError(f"{a.key}: no session name for prs.tsv; pass --session <name>")
        check_name(name, "session name")
        led["session"] = name
        tsv, table = prs_tsv(led), render_table(led)
    fsutil.atomic_write(str(profile.context_root() / "sessions" / name / "prs.tsv"), tsv)
    sys.stdout.write(table)
    return 0


def cmd_resume(a) -> int:
    repo_dir = str(Path(a.repo_dir).resolve())
    if not Path(repo_dir).is_dir():
        raise SwarmError(f"--repo-dir: no such directory: {repo_dir}")
    led = load(a.key)
    repo = a.repo or led.get("repo")
    heads = read_heads(repo_dir, a.remote)
    prs = read_prs(repo, repo_dir)
    trees = read_worktrees(repo_dir)
    extra = [clean_ticket(t) for t in a.ticket or []]

    def compare(ledger: dict) -> list[dict]:
        rows = ledger["tickets"]
        keys = [t for t, r in rows.items() if not r["repo"] or not repo or r["repo"] == repo]
        keys += [t for t in extra if t not in rows]
        return [compare_row(t, rows[t] if t in rows else new_row(t), heads, prs, trees) for t in keys]

    results = compare(led)
    differs = [r for r in results if r["changes"]]
    if a.apply and differs:
        with edit(a.key) as locked:  # compared again under the lock, against the remote state just read
            for res in compare(locked):
                if res["changes"]:
                    row = locked["tickets"].setdefault(res["ticket"], new_row(res["ticket"]))
                    restore_row(locked, res["ticket"], row, res["changes"])
    if a.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    for r in results:
        for field, (old, new) in r["changes"].items():
            print(f"{r['ticket']} {field}: {'-' if old is None else old} -> {new}")
        if r["action"] != "none":
            where = "".join(f" {k}={r[k]}" for k in ("branch", "worktree", "dirty", "pr") if r[k] is not None)
            print(f"{r['ticket']} action: {r['action']}{where}" + (f" ({r['note']})" if r["note"] else ""))
    if not differs:
        print(f"resume: the ledger and the remote agree on {a.key}")
    elif a.apply:
        print(f"resume: wrote {len(differs)} row(s)")
    else:
        print(f"resume: {len(differs)} row(s) differ; nothing written (--apply writes them)")
    return 0


def next_id(items: list[dict], prefix: str) -> str:
    return f"{prefix}{len(items) + 1}"


def cmd_grant(a) -> int:
    if not a.decision.strip():
        raise Refused("a grant needs its decision line")
    expires = utc_iso(a.expires)
    with edit(a.key) as led:
        grant = {"id": next_id(led["grants"], "g"), "scope": a.scope, "granted_by": a.by, "expires": expires,
                 "decision": a.decision.strip(), "at": now_iso()}
        led["grants"].append(grant)
        log(led, "grant", by=a.by, note=a.scope)
    print(f"{grant['id']}: {a.scope}, by {a.by}, until {expires}")
    return 0


def cmd_watch(a) -> int:
    with edit(a.key) as led:
        if a.close:
            found = [w for w in led["watches"] if w["id"] == a.close]
            if not found:
                raise Absent(f"{a.key}: no watch {a.close}")
            found[0]["closed"] = found[0].get("closed") or now_iso()
            print(f"{a.close}: closed")
            return 0
        if not (a.due and a.owner and a.what):
            raise SwarmError("watch needs --due, --owner and --what, or --close <id>")
        watch = {"id": next_id(led["watches"], "w"), "due": utc_iso(a.due), "owner": a.owner, "what": a.what, "closed": None}
        led["watches"].append(watch)
    print(f"{watch['id']}: due {watch['due']}")
    return 0


def cmd_decision(a) -> int:
    with edit(a.key) as led:
        if a.close:
            found = [d for d in led["decisions"] if d["id"] == a.close]
            if not found:
                raise Absent(f"{a.key}: no decision {a.close}")
            if not a.line:
                raise Refused(f"{a.close}: closing a decision needs its --line")
            found[0].update(closed=found[0].get("closed") or now_iso(), line=a.line)
            print(f"{a.close}: closed")
            return 0
        if not a.question:
            raise SwarmError("decision needs --question, or --close <id> --line <decision line>")
        item = {"id": next_id(led["decisions"], "d"), "question": a.question, "ticket": a.for_ticket, "opened": now_iso(),
                "closed": None, "line": None}
        led["decisions"].append(item)
    print(f"{item['id']}: open")
    return 0


def cmd_hold(a) -> int:
    scope = check_name(a.scope, "scope")
    with edit(a.key) as led:
        held = [h for h in live_holds(led) if h["scope"] == scope]
        if a.release:
            if not held:
                raise Absent(f"{a.key}: no hold on {scope}")
            if not a.by or a.by not in (held[0]["releaser"], "owner"):
                raise Refused(f"{scope}: only {held[0]['releaser']} or the owner ends this hold, not {a.by or 'an unnamed caller'}")
            held[0].update(released_at=now_iso(), released_by=a.by)
            log(led, "release", by=a.by, note=scope)
            print(f"{scope}: released by {a.by}")
            return 0
        if not (a.releaser and a.reason):
            raise SwarmError("hold needs --releaser and --reason, or --release --by <who>")
        if held:
            raise Refused(f"{scope}: already held; its releaser is {held[0]['releaser']}")
        led["holds"].append({"scope": scope, "releaser": a.releaser, "reason": a.reason, "at": now_iso(),
                             "released_at": None, "released_by": None})
        log(led, "hold", by=a.releaser, note=f"{scope}: {a.reason}")
    print(f"{scope}: held, releaser {a.releaser}")
    return 0


def cmd_budget(a) -> int:
    if not a.decision.strip():
        raise Refused("a budget change needs its decision line")
    if a.epic_usd is None and a.worker_usd is None and a.spent is None:
        raise SwarmError("budget needs --epic-usd, --worker-usd or --spent")
    with edit(a.key) as led:
        for field, value in (("epic_usd", a.epic_usd), ("worker_usd", a.worker_usd)):
            if value is not None:
                led["budget"][field] = value
        if a.spent is not None:
            led["spent"] = a.spent
        log(led, "budget", note=a.decision.strip())
        b = budget_state(led)
    print(f"{a.key}: budget {b['state']}, spent {b['spent']:.2f}")
    return 0


def cmd_show(a) -> int:
    led = load(a.key)
    print(json.dumps(row_of(led, clean_ticket(a.ticket)) if a.ticket else led, indent=2, ensure_ascii=False))
    return 0


# ── the CLI ─────────────────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="swarm.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def command(name: str, fn, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_, description=help_, allow_abbrev=False)
        p.set_defaults(fn=fn)
        p.add_argument("key", help="the epic key: the ledger's file name")
        return p

    p = command("init", cmd_init, "create the ledger")
    p.add_argument("--title")
    p.add_argument("--repo", help="owner/repo; a row's own --repo wins")
    p.add_argument("--session", help="the orchestrator's session name: where render writes prs.tsv")
    p.add_argument("--doc", help="the epic's context doc")
    p.add_argument("--waves", help="comma-separated wave names, in order")
    p.add_argument("--max-workers", type=int)
    p.add_argument("--epic-usd", type=float)
    p.add_argument("--worker-usd", type=float)

    p = command("add", cmd_add, "add a ticket's row (--update changes one)")
    p.add_argument("ticket")
    p.add_argument("--title")
    p.add_argument("--repo")
    p.add_argument("--area", action="append", help="a path the ticket owns; repeat")
    p.add_argument("--dep", action="append", help="a ticket that must be done first; repeat")
    p.add_argument("--sizing")
    p.add_argument("--wave")
    p.add_argument("--dw", action="append", help="a DW-<n> id; repeat")
    p.add_argument("--ticket-file", help="read the DW ids from this ticket's `## Done when` (`-` for stdin)")
    p.add_argument("--cap-usd", type=float)
    p.add_argument("--incident", action="store_true", help="an incident lane: starts past the plan and leaves the go in place")
    p.add_argument("--already-done", action="store_true", help="a ticket closed before this ledger: a dependency to count as done")
    p.add_argument("--update", action="store_true")

    p = command("approve", cmd_approve, "the owner's go on the plan")
    p.add_argument("--decision", required=True, help="the decision line (docs/carousel.md § After the answer)")

    p = command("assign", cmd_assign, "a worker takes the row, or a re-brief that moves no row")
    p.add_argument("ticket")
    p.add_argument("--kind", choices=WORKER_KINDS)
    p.add_argument("--name", help="the worker's lane: the from= on its lines")
    p.add_argument("--ref")
    p.add_argument("--worktree")
    p.add_argument("--branch")
    p.add_argument("--cap-usd", type=float)
    p.add_argument("--rebrief", action="store_true", help="ASSIGN rebrief=1: the row does not move")

    p = command("status", cmd_status, "the orchestrator's moves of a row")
    p.add_argument("ticket")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--pr", type=int, help="the PR number (status pr, merged)")
    p.add_argument("--head", help="the head: for ready-to-merge, the READY head the gates held on")
    p.add_argument("--sha", help="the merged commit (status merged)")
    p.add_argument("--merged-by", choices=MERGED_BY, default="orchestrator", help="an owner or auto merge may reach a row at pr")
    p.add_argument("--reason", help="why (status parked; status blocked, a FAIL on the merged commit)")

    p = command("progress", cmd_progress, "a new commit, status or comment; spend; a counted stall")
    p.add_argument("ticket")
    p.add_argument("--head")
    p.add_argument("--spent", type=float, help="the worker's spend so far, USD; not progress on its own")
    p.add_argument("--stall", action="store_true", help="count a stall; the stall window restarts")
    p.add_argument("--note")

    p = command("pass", cmd_pass, "the verifier records one DW-<n>; refused without --evidence or without --by verifier")
    p.add_argument("ticket")
    p.add_argument("dw", metavar="DW-<n>")
    p.add_argument("--evidence", required=True, help="what the verifier saw on the merged commit")
    p.add_argument("--by", required=True, help="must be `verifier`")

    p = command("apply", cmd_apply, "apply one CLAIMED, PR, READY or BLOCKED line")
    p.add_argument("line", help="the grammar line, as one argument")

    p = command("tick", cmd_tick, "the orchestrator's input; writes nothing")
    p.add_argument("--json", action="store_true")
    p.add_argument("--now", help="the time to compare against (default: the wall clock)")

    p = command("render", cmd_render, "print the `## Swarm` section and write sessions/<name>/prs.tsv")
    p.add_argument("--session", help="saved to the ledger; default: the ledger's, then the running session's registered name")

    p = command("resume", cmd_resume, "compare the ledger with the remote; writes nothing without --apply")
    p.add_argument("--repo-dir", default=".", help="a checkout of the repo (default: here)")
    p.add_argument("--remote", default="origin")
    p.add_argument("--repo", help="owner/repo (default: the ledger's)")
    p.add_argument("--ticket", action="append", help="a ticket the ledger has no row for; repeat")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--json", action="store_true")

    p = command("grant", cmd_grant, "record an owner's grant")
    p.add_argument("--scope", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--expires", required=True)
    p.add_argument("--decision", required=True, help="the decision line")

    p = command("watch", cmd_watch, "add a watch, or --close one")
    p.add_argument("--due")
    p.add_argument("--owner")
    p.add_argument("--what")
    p.add_argument("--close", metavar="ID")

    p = command("decision", cmd_decision, "open a decision, or --close one")
    p.add_argument("--question")
    p.add_argument("--for-ticket")
    p.add_argument("--close", metavar="ID")
    p.add_argument("--line", help="the decision line that closes it")

    p = command("hold", cmd_hold, "record a HOLD, or --release it")
    p.add_argument("scope", help="a ticket, a lane, a resource or the epic key")
    p.add_argument("--releaser")
    p.add_argument("--reason")
    p.add_argument("--release", action="store_true")
    p.add_argument("--by", help="who releases: the named releaser or the owner")

    p = command("budget", cmd_budget, "change a cap, on the owner's decision line; or record the epic's spend")
    p.add_argument("--epic-usd", type=float)
    p.add_argument("--worker-usd", type=float)
    p.add_argument("--spent", type=float)
    p.add_argument("--decision", required=True)

    p = command("show", cmd_show, "print the ledger, or one row, as JSON")
    p.add_argument("ticket", nargs="?")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except SwarmError as e:
        print(f"swarm: {e}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"swarm: {e}", file=sys.stderr)
        return 2
    except SystemExit as e:  # an invalid config.json: one line naming the file, and the I/O exit code
        if isinstance(e.code, str):
            print(f"swarm: {e.code}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
