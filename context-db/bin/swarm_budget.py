#!/usr/bin/env python3
"""swarm_budget.py — the swarm's budget brake: what each worker and the epic spent, who is past a cap, the hard stop.

`docs/swarm.md` is the contract: § The swarm tick step 6, § Authority matrix (only the owner raises a cap) and § STOP and
the call in flight. `swarm.py tick` calls `sweep` inside the orchestrator's session; nothing runs between sessions. Spend is
counted at each return and each tick, so a cap can be overshot by one step. `swarm.py` holds the one dollar flag
(`budget_state`): this script writes the `spent` fields it reads, and adds the breaches a dollar figure cannot show.

Every subcommand takes the epic key first:
  sweep    read every worker and the orchestrator, write `spent`, report the breaches. With `swarm.budget.hard_stop` on, a
           breach writes `STOP-<key>` through `lease.py stop`, prints what that says, and prints one line per running subagent:
           `task-stop <ticket> task=<agent id>` for the orchestrator's `TaskStop`, or `task-stop-unknown <ticket>` when its ref
           gives no id and that worker was not reached. It never touches a pid. --session-id names the orchestrator's session;
           without it this session is the orchestrator only if its registry row says `role: orchestrator` on this epic. Exit 2
           when the stop could not be written.
  status   the same report from what the ledger holds now. Writes nothing and reads no transcript
  digest   the digest's budget lines, a stop's line among them. Writes nothing
  caps     the four cap fields an assignment file carries, resolved for one ticket. --forks, --minutes and --split-turns set
           that assignment's own; a raise needs the owner's --decision line, a first or a lower cap needs none. The dollar
           cap is set by `swarm.py assign --cap-usd`
  forget   the owner's repair for a session recorded as an orchestrator by mistake: drop it from the map, log it, recompute the
           epic's `spent`. A STOP it caused stays until `lease.py go`

**The caps.** `spend_usd`, `forks`, `minutes` and `split_turns` are the field names an assignment file and the worker brief
use. Each resolves assignment, then config, then default (the dollar cap has the ledger's `swarm.py budget` value between
them): `swarm.budget.worker_usd` none, `swarm.budget.worker_forks` 4, `swarm.budget.worker_minutes` none,
`swarm.budget.split_turns` 10. `swarm.budget.epic_usd` (none) is the epic's and `swarm.budget.hard_stop` (false) arms the
stop. A key set to null is no cap, whichever key it is. A headless worker takes `spend_usd` as `--max-budget-usd`.

**A breach.** `spend` at or past the cap, `forks` or `minutes` past it, and `split`: the stats line's split hint on for
`split_turns` consecutive turns, for a worker or for the orchestrator. Forks, minutes and split count only a worker that
is running. The worker finishes the step, pushes, sends `BLOCKED <ticket> budget orchestrator` and starts nothing new
(`docs/delegation.md` § The worker brief). Only the owner raises a cap.

**A reading.** One record per transcript under the row's `spend`, keyed by the worker's `ref` (a session id, a subagent's
agent id or a transcript's absolute path): `usage` is `session_stats.py`'s TOTAL, plus for a subagent the forks its own
`Agent` calls made; `reported` is `total_cost_usd` from a headless worker's result, `<state/swarm>/<key>/<worker>/result.json`.
`spent` is the higher of the two and no reading ever lowers it, so a lost transcript or a harness estimate under the other
figure cannot free budget. The row's `spent` is the sum of its readings. The epic's `spent` is each orchestrator session's
TOTAL, which already holds that session's subagent workers, plus everything spent outside those sessions.

**A meter.** One per transcript, `spend.meters[<ref>]`: the totals last seen and the row that holds it. A row counts what the
meter grew by while it held it, so each dollar and each fork lands on one row. A peer's session holds work from before its
assignment, so its meter starts at the totals of the first sweep that sees it, and work between the assignment and that sweep
goes uncounted. A subagent's or a headless worker's transcript starts with the assignment, so its meter starts at zero. A
second row naming the ref takes the meter at its first sweep; the row before it keeps what it counted and stops counting.

**An orchestrator.** A session is recorded on the epic only when `--session-id` names it or its registry row says
`role: orchestrator` on this epic, so one tick from another session cannot put that session's total on the figure.

**Failing closed.** A cap that does not parse, a stop not written and an epic cap with no orchestrator reading this sweep refuse
the next spawn (`refuse`). A `hard_stop` that is neither true, false nor null reads as true. A worker's transcript that cannot
be read is a reported line (`spend-unread`) and closes nothing: that worker keeps its last figure until a reading or
`swarm.py progress --spent` gives another.

Another script imports `sweep(key)`, `assess(led)`, `tick_findings(report)`, `digest_lines(led, report)`,
`row_caps(led, row)` and `CAP_FIELDS`.

Exit codes: 0 ok; 1 no such ledger or ticket; 2 bad usage, a failed read, a stop not written; 3 refused by a rule. Stdlib only.
"""
from __future__ import annotations
import argparse
import glob
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_profile as profile  # noqa: E402 — the one content-root resolver and config reader
import lease  # noqa: E402 — the one writer of a STOP file
import session_stats  # noqa: E402 — the one spend figure and the one split hint
import swarm  # noqa: E402 — the task ledger and its one dollar flag
import transcripts  # noqa: E402 — the one transcript reader

CAP_FIELDS = ("spend_usd", "forks", "minutes", "split_turns")
CONFIG_DEFAULTS = {"epic_usd": None, "worker_usd": None, "worker_forks": 4, "worker_minutes": None, "split_turns": 10}
CAP_CONFIG = {"forks": "worker_forks", "minutes": "worker_minutes", "split_turns": "split_turns"}
METER_FIGURES = ("usage", "forks", "reported")
AGENT_FILE = re.compile(r"agent-(.+)\.jsonl")
RESULT_FILE = "result.json"
COST_FIELD = "total_cost_usd"
META_SUFFIX = ".meta.json"
READ_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError)  # a transcript the readers choke on is unread, not zero


# ── caps ────────────────────────────────────────────────────────────────────────────────────
def config_caps() -> tuple[dict, list[str]]:
    """The `swarm.budget` numbers, and a line per one that does not parse. Null is no cap, for every key. A bad value keeps
    its default here: the line is what fails closed, because `assess` refuses the next spawn on it."""
    values, errors = {}, []
    for name, default in CONFIG_DEFAULTS.items():
        raw = profile.get(f"swarm.budget.{name}", default)
        if raw is None:
            values[name] = None
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0:
            values[name] = raw
        else:
            values[name] = default
            errors.append(f"swarm.budget.{name} is not a number ({raw!r})")
    return values, errors


def hard_stop_setting() -> tuple[bool, str | None]:
    """Off only for false or null: a value nobody can read arms the stop and says so."""
    raw = profile.get("swarm.budget.hard_stop", False)
    if raw is None or raw is False:
        return False, None
    if raw is True:
        return True, None
    return True, f"swarm.budget.hard_stop is not true or false ({raw!r}); read as true"


def row_caps(led: dict, row: dict, cfg: dict | None = None) -> dict:
    """The four caps one assignment runs under: its own, then the config's or the default."""
    cfg = cfg if cfg is not None else config_caps()[0]
    own = row.get("caps") or {}
    caps = {"spend_usd": swarm.row_cap(led, row)}
    for field, name in CAP_CONFIG.items():
        caps[field] = own[field] if own.get(field) is not None else cfg[name]
    return caps


# ── the readers ─────────────────────────────────────────────────────────────────────────────
def locate(ref: str) -> str | None:
    """The transcript a worker's `ref` names: an absolute path, a session id or a subagent's agent id."""
    if os.path.isabs(ref):
        return ref if os.path.isfile(ref) else None
    if not swarm.NAME_RE.fullmatch(ref):
        return None
    found = transcripts.find_transcript(ref)
    if found:
        return found
    base = os.path.join(glob.escape(transcripts.projects_dir()), "*", "*", "subagents")
    hits = glob.glob(os.path.join(base, f"agent-{ref}.jsonl")) + glob.glob(os.path.join(base, f"{ref}.jsonl"))
    return hits[0] if hits else None


def owning_session(path: str) -> str | None:
    """The session a subagent's transcript sits under, or None for a session's own transcript."""
    parent = Path(path).parent
    return parent.parent.name if parent.name == "subagents" else None


def fork_files(path: str) -> list[str]:
    """The transcripts of the forks a subagent made, nested ones included. A session's TOTAL already holds its forks, but a
    subagent's sit beside it, each with a `.meta.json` that names the `Agent` call behind it. With no such file a fork is
    not attributed to the worker. The epic's `spent` still holds it, in the orchestrator's TOTAL."""
    directory = os.path.dirname(path)
    if os.path.basename(directory) != "subagents":
        return []
    calls: dict[str, str] = {}
    for meta in glob.glob(os.path.join(glob.escape(directory), f"*{META_SUFFIX}")):
        try:
            call = json.loads(Path(meta).read_text(encoding="utf-8")).get("toolUseId")
        except (OSError, ValueError, AttributeError):
            continue
        if call:
            calls[call] = meta[:-len(META_SUFFIX)] + ".jsonl"
    found, todo = [], [path]
    while todo:
        for o in transcripts.records(todo.pop()):
            message = o.get("message") if o.get("type") == "assistant" else None
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Agent":
                    child = calls.pop(block.get("id"), None)
                    if child and os.path.isfile(child):
                        found.append(child)
                        todo.append(child)
    return found


def split_streak(path: str) -> int:
    """How many turns in a row, up to the last one, the stats line carried the split hint. Replays `session_stats.collect`'s
    per-turn prefix and asks `session_stats.split_hint` at each turn, so the threshold, the window and the reset at an
    auto-compact stay in one place."""
    series: list[int] = []
    position: dict[str, int] = {}
    start = 0
    for o in transcripts.records(path):
        if o.get("isCompactSummary"):
            start = len(series)
        if o.get("type") != "assistant":
            continue
        m = o.get("message")
        m = m if isinstance(m, dict) else {}
        rid, usage = o.get("requestId") or m.get("id"), m.get("usage")
        if not (rid and usage and isinstance(usage, dict)):
            continue
        i, cw, cr, _ = transcripts.tokens(usage)
        if rid in position:
            series[position[rid]] = i + cw + cr
        else:
            position[rid] = len(series)
            series.append(i + cw + cr)
    turns = series[start:]
    window, threshold = session_stats.split_window(), session_stats.split_threshold()
    streak = 0
    for end in range(len(turns), 0, -1):
        recent = turns[max(0, end - window):end]
        prefix = {"avg": int(sum(recent) / len(recent)), "window": window, "turns": end, "threshold": threshold}
        if session_stats.split_hint({"recent_prefix": prefix}) is None:
            break
        streak += 1
    return streak


def result_path(key: str, worker: str) -> Path:
    return swarm.state_dir() / key / swarm.check_name(worker, "worker name") / RESULT_FILE


def read_result(path: Path) -> tuple[float | None, str | None]:
    """`total_cost_usd` from a headless run's result: `--output-format json`, or the last such line of `stream-json`. No
    file means the run has not ended, which is no reading and no problem. A file with no usable figure is a problem."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"{path}: {e}"
    try:
        parsed = json.loads(text)
        items = parsed if isinstance(parsed, list) else [parsed]
    except ValueError:
        items = []
        for line in text.splitlines():
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    costs = [o[COST_FIELD] for o in items if isinstance(o, dict) and COST_FIELD in o]
    if not costs:
        return None, f"{path}: no {COST_FIELD}"
    cost = costs[-1]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        return None, f"{path}: {COST_FIELD} is not an amount ({cost!r})"
    return float(cost), None


def read_worker(key: str, row: dict) -> tuple[dict, str | None]:
    """One worker's reading, and why part of it could not be read. A reading with no figure writes nothing."""
    w = row["worker"]
    name, ref, kind = w.get("name"), w.get("ref"), w.get("kind")
    reading = {"worker": name, "kind": kind, "usage": None, "reported": None, "forks": None, "split_streak": None, "under": None}
    problems = []
    path = locate(ref) if ref else None
    if path:
        try:
            stats = session_stats.collect(path)
            forks_spent = sum(session_stats.collect(f)["spend_usd_est"] for f in fork_files(path))
            reading.update(usage=stats["spend_total_usd_est"] + forks_spent, forks=stats["subagents"],
                           split_streak=split_streak(path), under=owning_session(path))
        except READ_ERRORS as e:
            problems.append(f"{path}: {e!r}")
    elif ref:
        problems.append(f"no transcript for ref {ref}")
    elif kind != "headless":
        problems.append("no ref on the row: `swarm.py assign --ref` names the transcript")
    if kind == "headless":
        try:
            reading["reported"], problem = read_result(result_path(key, name))
        except swarm.SwarmError as e:
            problem = str(e)
        if problem:
            problems.append(problem)
        elif reading["usage"] is None and reading["reported"] is None:
            problems.append("no transcript and no result yet")
    return reading, "; ".join(problems) or None


def read_orchestrator(session_id: str) -> tuple[dict | None, str | None]:
    path = session_stats.find_transcript(session_id) if swarm.NAME_RE.fullmatch(session_id) else None
    if not path:
        return None, f"no transcript for session {session_id}"
    try:
        return {"total": session_stats.collect(path)["spend_total_usd_est"], "split_streak": split_streak(path)}, None
    except READ_ERRORS as e:
        return None, f"{path}: {e!r}"


def orchestrator_session(key: str, named: str | None) -> tuple[str | None, str | None]:
    """The session to record as this epic's orchestrator, or why there is none. Being the one that runs the tick is not
    enough: a stray session's total would stay on the epic's figure. The registry is `lease.py`'s, the role and epic check
    its `stop_for` uses."""
    if named:
        return named, None
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not session_id:
        return None, "no session id: run this in the orchestrator's session or pass --session-id"
    rows = lease.session_rows(profile.context_root(), session_id)
    if any(role == "orchestrator" and (epic == key or lease.epic_matches(key, epic, repos)) for _, epic, repos, role in rows):
        return session_id, None
    return None, f"session {session_id} has no registry row with role: orchestrator on {key}: register the role or pass --session-id"


# ── the ledger's spend ──────────────────────────────────────────────────────────────────────
def reading_key(row: dict) -> str | None:
    w = row["worker"]
    return w.get("ref") or w.get("name")


def merge_reading(row: dict, key: str, new: dict, grown: dict, now: str) -> None:
    """A transcript's growth onto the row. Spend and forks only go up, and the row's `spent` is the sum of its readings: a
    second worker on the row starts its own transcript from nothing."""
    spend = row.setdefault("spend", {})
    old = spend.get(key) or {}
    usage = (old.get("usage") or 0) + grown["usage"]
    reported = (old.get("reported") or 0) + grown["reported"]
    merged = {"worker": new["worker"], "kind": new["kind"], "usage": round(usage, 6), "reported": round(reported, 6),
              "spent": round(max(usage, reported), 2), "forks": (old.get("forks") or 0) + grown["forks"],
              "split_streak": old.get("split_streak", 0) if new["split_streak"] is None else new["split_streak"],
              "under": new["under"] or old.get("under")}
    if any(old.get(k) != v for k, v in merged.items()):
        spend[key] = {**merged, "at": now}
    row["spent"] = max(row.get("spent") or 0, round(sum(r["spent"] for r in spend.values()), 2))


def meter_reading(led: dict, key: str, named: dict[str, dict], now: str) -> None:
    """One transcript's totals, onto the rows that name it (`named`: ticket to reading). A first sighting starts the meter, at
    the totals for a peer and at zero otherwise. The growth goes to the row holding the meter, then a row new to this ref
    takes it. Only the holder carries the split streak, so a ref on two rows flags once."""
    rows = led["tickets"]
    meters = led.setdefault("spend", {"orchestrators": {}, "current": None}).setdefault("meters", {})
    totals = next(iter(named.values()))
    meter = meters.get(key)
    if meter is None:
        start = totals if totals["kind"] == "peer" else {}
        meter = meters[key] = {"holder": list(named)[-1], **{f: start.get(f) or 0 for f in METER_FIGURES}}
    grown = {f: max(0, (totals[f] or 0) - meter[f]) for f in METER_FIGURES}
    payee = meter["holder"]
    new_rows = [t for t in named if key not in (rows[t].get("spend") or {})]
    meter.update({f: max(meter[f], totals[f] or 0) for f in METER_FIGURES}, holder=new_rows[-1] if new_rows else payee)
    for t in {*named, payee}:
        reading = named.get(t) or rows[t]["spend"][key]  # the row that held it can have ended: it is not read, it is paid
        streak = reading["split_streak"] if t == meter["holder"] else 0
        merge_reading(rows[t], key, {**reading, "split_streak": streak}, grown if t == payee else dict.fromkeys(METER_FIGURES, 0), now)


def epic_spend(led: dict) -> float:
    """Each orchestrator session's TOTAL, plus what the rows hold outside those sessions: a subagent worker's readings sit
    inside its orchestrator's TOTAL and count once. A ref on two rows counts once too: its meter pays one row at a time."""
    sessions = (led.get("spend") or {}).get("orchestrators") or {}
    total = sum(s.get("total") or 0 for s in sessions.values())
    for row in led["tickets"].values():
        inside = sum(r.get("spent") or 0 for r in (row.get("spend") or {}).values() if r.get("under") in sessions)
        total += max(0, (row.get("spent") or 0) - inside)
    return round(total, 2)


def write_readings(led: dict, readings: dict, session_id: str | None, orchestrator: dict | None, now: str) -> None:
    by_ref: dict[str, dict[str, dict]] = {}
    for ticket, (key, reading) in readings.items():
        by_ref.setdefault(key, {})[ticket] = reading
    for key, named in by_ref.items():
        meter_reading(led, key, named, now)
    if orchestrator:
        spend = led.setdefault("spend", {"orchestrators": {}, "current": None})
        old = spend["orchestrators"].get(session_id) or {}
        spend["orchestrators"][session_id] = {"total": max(old.get("total") or 0, orchestrator["total"]),
                                              "split_streak": orchestrator["split_streak"]}
        spend["current"] = session_id
    if readings or orchestrator:
        led["spent"] = max(led.get("spent") or 0, epic_spend(led))


def forget(key: str, session_id: str) -> dict:
    """Off the map, and the figure with it. `write_readings` only ever raises `led.spent`, so the lower figure stands. What
    the owner put on the ledger by hand beyond the map stays: the figure falls by what the session added, no further. A
    subagent worker under that session stays counted, now as spend outside the map."""
    with swarm.edit(key) as led:
        spend = led.get("spend") or {}
        sessions = spend.get("orchestrators") or {}
        if session_id not in sessions:
            raise swarm.Absent(f"{key}: no orchestrator session {session_id} on the ledger")
        before, was = epic_spend(led), led.get("spent") or 0
        dropped = sessions.pop(session_id)
        if spend.get("current") == session_id:
            spend["current"] = None
        after = epic_spend(led)
        led["spent"] = round(max(after, was - (before - after)), 2)
        swarm.log(led, "forget", note=f"session {session_id}: ${dropped.get('total') or 0:.2f} off the map, "
                                      f"spent ${was:.2f} to ${led['spent']:.2f}")
    return {"session": session_id, "dropped": dropped.get("total") or 0, "was": was, "spent": led["spent"]}


# ── the report ──────────────────────────────────────────────────────────────────────────────
def run_minutes(led: dict, ticket: str, row: dict, moment: datetime) -> int:
    """Minutes since this run began: the last assignment or re-brief, so the wait between a READY and a re-brief is no
    worker's time."""
    started = next((e["at"] for e in reversed(led["log"]) if e.get("ticket") == ticket and e.get("act") in ("assign", "rebrief")),
                   row.get("last_progress") or led["created"])
    return max(0, int((moment - swarm.parse_time(started)).total_seconds() / 60))


def describe(b: dict) -> str:
    who = {"worker": b.get("ticket"), "orchestrator": "the orchestrator", "epic": "the epic"}[b["scope"]]
    if b["kind"] == "spend":
        return f"{who} spent ${b['value']:.2f} of ${b['cap']:.2f}"
    did = {"forks": "made {} forks", "minutes": "ran {} minutes", "split": "carried the split hint for {} turns"}[b["kind"]]
    return f"{who} {did.format(b['value'])}, cap {b['cap']}"


def assess(led: dict, now: datetime | None = None, unread: list[dict] | None = None) -> dict:
    """Who is past a cap and what may not spawn, from the ledger alone. `unread` is a sweep's failed reads; without one, a
    worker with no reading counts as unread, and so does the orchestrator under an epic cap."""
    moment = now or datetime.now(timezone.utc)
    cfg, errors = config_caps()
    hard_on, hard_error = hard_stop_setting()
    if hard_error:
        errors.append(hard_error)
    budget = swarm.budget_state(led)
    rows = led["tickets"]
    running = swarm.active_tickets(led)
    breaches = []
    if budget["state"] == "over":
        breaches.append({"scope": "epic", "kind": "spend", "value": budget["spent"], "cap": budget["epic_usd"]})
    spend = led.get("spend") or {}
    own = (spend.get("orchestrators") or {}).get(spend.get("current")) or {}
    if cfg["split_turns"] is not None and own.get("split_streak") and own["split_streak"] >= cfg["split_turns"]:
        breaches.append({"scope": "orchestrator", "kind": "split", "value": own["split_streak"], "cap": cfg["split_turns"]})
    breaches += [{"scope": "worker", "ticket": o["ticket"], "worker": rows[o["ticket"]]["worker"].get("name"), "kind": "spend",
                  "value": o["spent"], "cap": o["cap"]} for o in budget["over"]]
    workers, missing = [], []
    for t, r in rows.items():
        if not r["worker"].get("name"):
            continue
        caps = row_caps(led, r, cfg)
        reading = (r.get("spend") or {}).get(reading_key(r))
        minutes = run_minutes(led, t, r, moment) if t in running else None
        forks, streak = (reading or {}).get("forks") or 0, (reading or {}).get("split_streak") or 0
        workers.append({"ticket": t, "worker": r["worker"]["name"], "kind": r["worker"].get("kind"), "ref": r["worker"].get("ref"),
                        "status": r["status"], "running": t in running, "spent": r.get("spent") or 0, "caps": caps,
                        "forks": forks, "minutes": minutes, "split_streak": streak, "read": reading is not None})
        if reading is None:
            missing.append({"who": t, "why": "no reading yet: `swarm_budget.py sweep` reads it"})
        if t not in running:
            continue
        common = {"scope": "worker", "ticket": t, "worker": r["worker"]["name"]}
        if caps["forks"] is not None and forks > caps["forks"]:
            breaches.append({**common, "kind": "forks", "value": forks, "cap": caps["forks"]})
        if caps["minutes"] is not None and minutes > caps["minutes"]:
            breaches.append({**common, "kind": "minutes", "value": minutes, "cap": caps["minutes"]})
        if caps["split_turns"] is not None and streak and streak >= caps["split_turns"]:
            breaches.append({**common, "kind": "split", "value": streak, "cap": caps["split_turns"]})
    if budget["epic_usd"] is not None and not own:
        missing.append({"who": "orchestrator", "why": "no reading yet: `swarm_budget.py sweep --session-id` reads it"})
    unread = missing if unread is None else unread
    refuse = [f"budget config: {e}" for e in errors]
    return {"key": led["key"], "now": moment.astimezone(timezone.utc).strftime(swarm.LEDGER_TIME), "budget": budget,
            "workers": workers, "orchestrator": own, "breaches": breaches, "unread": unread, "config_errors": errors,
            "hard_stop": {"on": hard_on, "state": "none", "reason": None, "lines": []}, "task_stops": [], "refuse": refuse}


def task_id(ref: str | None) -> str | None:
    """What `TaskStop` takes for a subagent, its agent id: a bare ref, or a path whose file name is `agent-<id>.jsonl`. Any
    other path, or no ref, gives none."""
    if not ref:
        return None
    if os.path.isabs(ref):
        named = AGENT_FILE.fullmatch(os.path.basename(ref))
        return named.group(1) if named else None
    return ref[len("agent-"):] if ref.startswith("agent-") else ref


def apply_hard_stop(key: str, report: dict) -> None:
    """With the stop armed and a breach open: `STOP-<key>`, whoever is running told by id. A STOP already there stands, its
    reason and setter kept. A stop that cannot be written says so and refuses the next spawn: it never reads as written."""
    stop = report["hard_stop"]
    if not (stop["on"] and report["breaches"]):
        return
    first, more = report["breaches"][0], len(report["breaches"]) - 1
    stop["reason"] = f"budget: {describe(first)}" + (f" (and {more} more)" if more else "")
    if key in swarm.read_stops():
        stop["state"] = "already"
    else:
        try:
            stop["lines"] = lease.stop(profile.context_root(), key, stop["reason"], "orchestrator")
            stop["state"] = "written"
        except lease.Refused:  # the owner's STOP landed between the check and the write: it stands
            stop["state"] = "already"
        except (lease.Usage, OSError) as e:
            stop.update(state="failed", error=str(e))
    if stop["state"] == "failed":
        report["refuse"].append(f"hard stop not written: {stop['error']}")
        return
    report["task_stops"] = [{"ticket": w["ticket"], "task": task_id(w["ref"])} for w in report["workers"]
                            if w["running"] and w["kind"] == "subagent"]


def sweep(key: str, session_id: str | None = None, now: datetime | None = None) -> dict:
    """One budget step of the tick: read, write `spent`, assess, stop if armed. The transcripts are read outside the
    ledger's lock, because they can be large and the lock is shared with every other writer."""
    moment = now or datetime.now(timezone.utc)
    led = swarm.load(key)
    readings, unread = {}, []
    for t, r in led["tickets"].items():
        ended = r["status"] in ("verified", "done") and reading_key(r) in (r.get("spend") or {})
        if not r["worker"].get("name") or ended:
            continue
        reading, why = read_worker(key, r)
        if reading["usage"] is not None or reading["reported"] is not None:
            readings[t] = (reading_key(r), reading)
        if why:
            unread.append({"who": t, "why": why})
    orchestrator = None
    session_id, why = orchestrator_session(key, session_id)
    if session_id:
        orchestrator, why = read_orchestrator(session_id)
    if why:
        unread.append({"who": "orchestrator", "why": why})
    stamp = moment.astimezone(timezone.utc).strftime(swarm.LEDGER_TIME)
    with swarm.edit(key) as locked:
        write_readings(locked, {t: v for t, v in readings.items() if t in locked["tickets"]}, session_id, orchestrator, stamp)
    report = assess(swarm.load(key), moment, unread)
    if why and report["budget"]["epic_usd"] is not None:
        report["refuse"].append(f"no orchestrator reading under the epic cap: {why}")
    apply_hard_stop(key, report)
    if report["hard_stop"]["state"] == "written":
        with swarm.edit(key) as locked:
            swarm.log(locked, "hard-stop", note=report["hard_stop"]["reason"])
    return report


def report_lines(report: dict, breaches: list[dict]) -> list[str]:
    """What these breaches, the unread readings and a stop print as, here and in `swarm.py tick`."""
    lines = [f"over-budget {x.get('ticket') or x['scope']} kind={x['kind']} value={x['value']} cap={x['cap']}" for x in breaches]
    lines += [f"spend-unread {u['who']} why={json.dumps(u['why'], ensure_ascii=False)}" for u in report["unread"]]
    stop = report["hard_stop"]
    if stop["state"] != "none":
        lines.append(f"hard-stop {report['key']} {stop['state']} reason={json.dumps(stop.get('error') or stop['reason'], ensure_ascii=False)}")
    lines += stop["lines"]
    lines += [f"task-stop {t['ticket']} task={t['task']}" if t["task"] else f"task-stop-unknown {t['ticket']}" for t in report["task_stops"]]
    return lines


def tick_findings(report: dict) -> dict:
    """What `swarm.py tick` adds to its report. `no_assign` closes the pool: a breach no dollar figure shows, a refusal.
    A dollar breach is the tick's own, through `budget_state`, so it is left out here and prints once."""
    others = [x for x in report["breaches"] if x["kind"] != "spend"]
    return {"no_assign": [f"over budget: {describe(x)}" for x in others] + report["refuse"], "over": others,
            "unread": report["unread"], "hard_stop": report["hard_stop"], "task_stops": report["task_stops"],
            "lines": report_lines(report, others)}


def digest_lines(led: dict, report: dict) -> list[str]:
    """The digest's budget block (`docs/swarm.md` § The swarm tick, step 9): spend against caps, then what stopped and who
    must act."""
    b = report["budget"]
    cap = "no epic cap" if b["epic_usd"] is None else f"of ${b['epic_usd']:.2f}"
    out = [f"Budget: ${b['spent']:.2f} {cap}, {len(report['breaches'])} over."]
    for w in report["workers"]:
        usd = "no cap" if w["caps"]["spend_usd"] is None else f"${w['caps']['spend_usd']:.2f}"
        forks = "no cap" if w["caps"]["forks"] is None else w["caps"]["forks"]
        spent = f"${w['spent']:.2f}" if w["read"] or w["spent"] else "?"  # a figure by hand is a figure; nothing at all is not $0
        out.append(f"- {swarm.ticket_ref(w['ticket'])} {w['worker']}: {spent} / {usd}, forks {w['forks'] if w['read'] else '?'} / {forks}")
    if report["unread"]:
        who = ["the orchestrator" if u["who"] == "orchestrator" else swarm.ticket_ref(u["who"]) for u in report["unread"]]
        out.append(f"Spend unread: {', '.join(who)}.")
    if b["state"] == "over":
        out.append("Epic cap reached: nothing spawns until the owner raises the cap or ends the epic.")
    stop = lease.stops(profile.context_root()).get(led["key"])
    if stop is not None:
        reason = stop.get("reason") or "no reason recorded"
        act = (f" The owner raises the cap or ends the epic, then `lease.py go {led['key']}` lifts it."
               if reason.startswith("budget") else "")
        out.append(f"STOP {led['key']} by the {stop.get('by', 'orchestrator')}: {reason}. Nothing spawns.{act}")
    out += [f"Over budget: {describe(x)}." for x in report["breaches"] if x["scope"] != "epic"]
    out += [f"No spawn: {r}." for r in report["refuse"]]
    return out


def print_report(report: dict) -> None:
    b, stop = report["budget"], report["hard_stop"]
    cap = "none" if b["epic_usd"] is None else f"{b['epic_usd']:.2f}"
    print(f"budget {report['key']} state={b['state']} spent={b['spent']:.2f} cap={cap} hard_stop={'on' if stop['on'] else 'off'}")
    for w in report["workers"]:
        c = w["caps"]
        print(f"spent {w['ticket']} worker={w['worker']} kind={w['kind'] or '-'} status={w['status']} spent={w['spent']:.2f} "
              f"cap={'-' if c['spend_usd'] is None else format(c['spend_usd'], '.2f')} forks={w['forks']}/{c['forks']} "
              f"minutes={'-' if w['minutes'] is None else w['minutes']}/{c['minutes']} split={w['split_streak']}/{c['split_turns']}")
    if report["orchestrator"]:
        print(f"orchestrator total={report['orchestrator']['total']:.2f} split={report['orchestrator']['split_streak']}")
    for line in report_lines(report, report["breaches"]):
        print(line)
    for r in report["refuse"]:
        print(f"no-spawn {r}")


# ── subcommands ─────────────────────────────────────────────────────────────────────────────
def cmd_sweep(a) -> int:
    report = sweep(a.key, a.session_id, swarm.parse_time(a.now) if a.now else None)
    print(json.dumps(report, indent=2, ensure_ascii=False)) if a.json else print_report(report)
    return 2 if report["hard_stop"]["state"] == "failed" else 0


def cmd_status(a) -> int:
    report = assess(swarm.load(a.key), swarm.parse_time(a.now) if a.now else None)
    print(json.dumps(report, indent=2, ensure_ascii=False)) if a.json else print_report(report)
    return 0


def cmd_digest(a) -> int:
    led = swarm.load(a.key)
    print("\n".join(digest_lines(led, assess(led, swarm.parse_time(a.now) if a.now else None))))
    return 0


def cmd_caps(a) -> int:
    ticket = swarm.clean_ticket(a.ticket)
    wanted = {f: v for f, v in (("forks", a.forks), ("minutes", a.minutes), ("split_turns", a.split_turns)) if v is not None}
    if any(v < 0 for v in wanted.values()):
        raise swarm.SwarmError("a cap cannot be negative")
    if wanted:
        decision = (a.decision or "").strip()
        with swarm.edit(a.key) as led:
            row = swarm.row_of(led, ticket)
            current = row_caps(led, row)
            raised = [f for f, v in wanted.items() if current[f] is not None and v > current[f]]
            if raised and not decision:
                raise swarm.Refused(f"{ticket}: raising {', '.join(raised)} needs the owner's --decision line")
            row.setdefault("caps", {}).update(wanted)
            swarm.log(led, "cap", ticket, note=decision or "a first or a lower cap")
    led = swarm.load(a.key)
    row = swarm.row_of(led, ticket)
    caps = row_caps(led, row)
    if a.json:
        print(json.dumps(caps, indent=2))
        return 0
    for field in CAP_FIELDS:
        print(f"{field}: {'none' if caps[field] is None else caps[field]}")
    if row["worker"].get("kind") == "headless" and caps["spend_usd"] is not None:
        print(f"flag: --max-budget-usd {caps['spend_usd']}")
    return 0


def cmd_forget(a) -> int:
    done = forget(a.key, a.session_id)
    print(f"forget {a.key} {done['session']} dropped={done['dropped']:.2f} spent={done['was']:.2f} to {done['spent']:.2f}")
    if a.key in swarm.read_stops():
        print(f"STOP-{a.key} stands: `lease.py go {a.key}` lifts it")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="swarm_budget.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def command(name: str, fn, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_, description=help_, allow_abbrev=False)
        p.set_defaults(fn=fn)
        p.add_argument("key", help="the epic key: the ledger's file name")
        return p

    p = command("sweep", cmd_sweep, "read every worker, write spent, report the breaches, stop if armed")
    p.add_argument("--session-id", help="the orchestrator's session (default: this session, if its registry row says role: orchestrator)")
    p.add_argument("--now", help="the time to compare against (default: the wall clock)")
    p.add_argument("--json", action="store_true")

    p = command("status", cmd_status, "the same report from what the ledger holds; writes nothing")
    p.add_argument("--now", help="the time to compare against (default: the wall clock)")
    p.add_argument("--json", action="store_true")

    p = command("digest", cmd_digest, "the digest's budget lines, a stop's line among them; writes nothing")
    p.add_argument("--now", help="the time to compare against (default: the wall clock)")

    p = command("caps", cmd_caps, "the four cap fields for one assignment; the flags set its own")
    p.add_argument("ticket")
    p.add_argument("--forks", type=int)
    p.add_argument("--minutes", type=int)
    p.add_argument("--split-turns", type=int)
    p.add_argument("--decision", help="the owner's decision line behind a raised cap")
    p.add_argument("--json", action="store_true")

    p = command("forget", cmd_forget, "drop a session from the orchestrators' map, log it, recompute the epic's spent")
    p.add_argument("session_id")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except swarm.SwarmError as e:
        print(f"swarm_budget: {e}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"swarm_budget: {e}", file=sys.stderr)
        return 2
    except SystemExit as e:  # an invalid config.json: one line naming the file, and the I/O exit code
        if isinstance(e.code, str):
            print(f"swarm_budget: {e.code}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
