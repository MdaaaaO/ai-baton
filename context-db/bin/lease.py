#!/usr/bin/env python3
"""lease.py — leases on serial resources and STOP files, the swarm contract's two stop-and-share mechanisms
(`docs/swarm.md` § Leases and holds, § STOP and the call in flight).

A serial resource (one only one holder can use at a time) is used only under a lease: one file per resource,
`<context root>/state/leases/<resource>.json`, holding each holder's lane, ref and pid, the acquire time, an expiry
(`swarm.lease_minutes`, default 120), the named releaser (`holder`, `orchestrator` or `owner`: who may release it) and
the reason. A resource with a capacity (`--capacity`, or `swarm.units.<resource>`) is shared and rate-limited: each
holder reserves `--units` of it and a reservation past the capacity is refused, so one worker cannot starve the rest.
`swarm.resources` (a list; empty = any name) names the resources the environment has.

  lease.py acquire <resource> [--lane L --ref R --pid N --minutes M --releaser W --reason T --units N]
        grant it, or exit 1 naming the holder. A holder whose pid is dead or whose expiry passed is free. The same
        lane acquiring again renews. After `release --hold` the same lane's terms are kept: a plain `acquire` on
        resume restores them (ref, units, releaser, reason) with no new assignment, or is refused when another
        holder has taken the resource by then (the worker sends `BLOCKED <ticket> lease <holder>`)
  lease.py release <resource> [--lane L] [--as holder|orchestrator|owner] [--hold] [--reason T]
        the named releaser or the owner releases; with `--hold` the terms are kept for the resume
  lease.py status [<resource>] [--json]      leases and STOP files
  lease.py sweep                             free every expired or dead-pid lease; one line each
  lease.py expire --lane L                   free that lane's expired leases; one RELEASE line each (heartbeat.sh)
  lease.py stop <scope> --reason T [--as orchestrator|owner]
        write `state/swarm/STOP-<scope>`: the PreToolUse hook (`ctx_adapter.py hook pre-tool-use`) then blocks the next
        work-tool call of a session whose lane holds the lease `<scope>` or works the epic `<scope>`; prints the
        in-flight rule
  lease.py go <scope> [--as orchestrator|owner]
        lift it: whoever set it, or the owner; a stop the owner set, only the owner

The lane is `--lane`, else the registered session name. Exit 0 ok · 1 refused or absent ·
2 usage or I/O error (one stderr line). Stdlib only."""
from __future__ import annotations
import argparse
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kit_profile  # noqa: E402  — same dir
from fsutil import atomic_write, locked  # noqa: E402  — same dir

RELEASERS = ("holder", "orchestrator", "owner")
STOP_SETTERS = ("orchestrator", "owner")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_MINUTES = 120
FMT = "%Y-%m-%dT%H:%M:%SZ"
IN_FLIGHT_RULE = (
    "A STOP cannot beat a call already in flight: it blocks the next work-tool call, never the running one. A worker\n"
    "reports what ran before the stop landed (BLOCKED <ticket> stop <who> ran=\"...\"), runs a long call on a serial\n"
    "resource in the background and ends it by its own task id on STOP, and nobody kills a peer's pid or stops work by\n"
    "a process-name pattern. The outbox send, the inbox drain, the ack, the lease release and ending the task stay open.")

# What the hook lets through after a STOP (docs/swarm.md § STOP and the call in flight): the swarm_msg verbs that send,
# drain, ack and read the inbox; the lease verbs that give a lease back or lift a stop; ending a task by its id.
EXEMPT_SCRIPTS = {"swarm_msg.py": {"send", "drain", "ack", "pending"},
                  "lease.py": {"release", "status", "go", "expire", "stop"}}
EXEMPT_TOOLS = ("TaskStop",)


class Refused(Exception):
    """A refusal the caller reads: exit 1."""


class Usage(Exception):
    """A usage or I/O error: exit 2."""


# ── paths and time ───────────────────────────────────────────────────────────────────────────────────────────

def lease_dir(root: Path) -> Path:
    return root / "state" / "leases"


def stop_dir(root: Path) -> Path:
    return root / "state" / "swarm"


def _name(kind: str, value: str) -> str:
    if not NAME_RE.match(value or "") or ".." in value:
        raise Usage(f"{kind} {value!r}: letters, digits, '.', '_' and '-' only")
    return value


def now() -> float:
    return time.time()


def fmt(ts: float) -> str:
    return time.strftime(FMT, time.gmtime(ts))


def parse(s: str) -> float:
    import calendar
    return float(calendar.timegm(time.strptime(s, FMT)))


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def expired(h: dict, at: float | None = None) -> bool:
    try:
        return (now() if at is None else at) >= parse(h["expires"])
    except (KeyError, ValueError, TypeError):
        return True  # an expiry nobody can read is no claim


def stale(h: dict) -> str:
    """Why a holder entry no longer holds ('dead pid' / 'expired'), or ''."""
    if not pid_alive(h.get("pid")):
        return "dead pid"
    if expired(h):
        return "expired"
    return ""


# ── config and identity ──────────────────────────────────────────────────────────────────────────────────────

def config_minutes() -> int:
    v = kit_profile.get("swarm.lease_minutes", DEFAULT_MINUTES)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else DEFAULT_MINUTES


def config_capacity(resource: str) -> int | None:
    units = kit_profile.get("swarm.units", {})
    v = units.get(resource) if isinstance(units, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def check_resource(resource: str) -> None:
    named = kit_profile.get("swarm.resources", [])
    if isinstance(named, list) and named and resource not in named:
        raise Usage(f"resource {resource!r} is not in swarm.resources ({', '.join(map(str, named))})")


def lane_of(arg: str | None) -> str:
    lane = (arg or kit_profile.session_name() or "").strip()
    if not lane:
        raise Usage("no lane: pass --lane or register the session")
    return _name("lane", lane)


# ── lease files ──────────────────────────────────────────────────────────────────────────────────────────────

def _path(root: Path, resource: str) -> Path:
    return lease_dir(root) / f"{_name('resource', resource)}.json"


def load(root: Path, resource: str) -> dict:
    p = _path(root, resource)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"resource": resource, "capacity": None, "holders": [], "parked": []}
    except (OSError, ValueError) as e:
        raise Usage(f"{p}: {e}") from e
    if not isinstance(d, dict):
        raise Usage(f"{p}: not a lease record")
    d.setdefault("resource", resource)
    d.setdefault("capacity", None)
    d.setdefault("holders", [])
    d.setdefault("parked", [])
    return d


def save(root: Path, d: dict) -> None:
    p = _path(root, d["resource"])
    if not d["holders"] and not d["parked"]:
        p.unlink(missing_ok=True)
        return
    atomic_write(str(p), json.dumps(d, indent=2, sort_keys=True) + "\n")


def resources(root: Path) -> list[str]:
    try:
        return sorted(p.stem for p in lease_dir(root).glob("*.json"))
    except OSError:
        return []


def _free_stale(d: dict) -> list[tuple[dict, str]]:
    """Drop the holders that no longer hold; returns them with the reason."""
    gone = [(h, stale(h)) for h in d["holders"]]
    gone = [(h, why) for h, why in gone if why]
    d["holders"] = [h for h in d["holders"] if not stale(h)]
    return gone


def held_units(d: dict) -> int:
    return sum(int(h.get("units") or 0) for h in d["holders"])


# ── verbs ────────────────────────────────────────────────────────────────────────────────────────────────────

def acquire(root: Path, a: argparse.Namespace) -> list[str]:
    resource = _name("resource", a.resource)
    check_resource(resource)
    lane = lane_of(a.lane)
    pid = a.pid
    if pid is None:
        owner = kit_profile._owning_pid()
        pid = int(owner) if owner and owner.isdigit() else None
    if pid is None:
        raise Usage("no owning claude process found: pass --pid")
    out: list[str] = []
    with locked(str(_path(root, resource))):
        d = load(root, resource)
        for h, why in _free_stale(d):
            out.append(f"freed {resource}: {h.get('lane')} ({why})")
        mine = next((h for h in d["holders"] if h.get("lane") == lane), None)
        parked = next((p for p in d["parked"] if p.get("lane") == lane), None)
        terms = {"ref": a.ref, "releaser": a.releaser, "reason": a.reason, "units": a.units}
        if parked:  # resume after HOLD: the terms the first acquire recorded, unless this call names its own
            terms = {k: (v if v is not None else parked.get(k)) for k, v in terms.items()}
        terms["releaser"] = terms["releaser"] or "holder"
        minutes = a.minutes if a.minutes is not None else config_minutes()
        cap = d["capacity"] if d["capacity"] is not None else (a.capacity or config_capacity(resource))
        if terms["units"] is not None and cap is None:
            raise Usage(f"--units needs a capacity: pass --capacity or set swarm.units.{resource}")
        units = (terms["units"] if terms["units"] is not None else 1) if cap is not None else None
        others = [h for h in d["holders"] if h is not mine]
        if cap is None and others:
            h = others[0]
            raise Refused(_held_line(resource, h) + ("" if not parked else
                          f"\nsend: BLOCKED {parked.get('ref') or resource} lease {h.get('lane')}"))
        if cap is not None:
            free = cap - sum(int(h.get("units") or 0) for h in others)
            if units > free:
                raise Refused(f"quota: {resource} has {free} of {cap} units free, {lane} asked for {units}"
                              + ("" if not parked else f"\nsend: BLOCKED {parked.get('ref') or resource} lease "
                                 f"{others[0].get('lane') if others else resource}"))
        entry = {"lane": lane, "ref": terms["ref"] or "", "pid": pid, "acquired": fmt(now()),
                 "expires": fmt(now() + 60 * minutes), "releaser": terms["releaser"],
                 "reason": terms["reason"] or ""}
        if cap is not None:
            entry["units"] = units
            d["capacity"] = cap
        d["holders"] = [h for h in d["holders"] if h is not mine] + [entry]
        d["parked"] = [p for p in d["parked"] if p.get("lane") != lane]
        save(root, d)
    out.append(f"{'renewed' if mine else 'acquired'} {resource} for {lane} until {entry['expires']}"
               + (f" ({units} of {cap} units)" if cap is not None else ""))
    return out


def _held_line(resource: str, h: dict) -> str:
    return (f"held: {resource} by {h.get('lane')} (ref {h.get('ref') or '-'}, pid {h.get('pid')}) "
            f"until {h.get('expires')}; releaser {h.get('releaser')}")


def release(root: Path, a: argparse.Namespace) -> list[str]:
    resource = _name("resource", a.resource)
    who = a.as_
    with locked(str(_path(root, resource))):
        d = load(root, resource)
        if not d["holders"]:
            raise Refused(f"{resource}: not held")
        if who == "holder":  # a holder gives back its own lease and no peer's
            target = lane_of(a.lane)
        elif a.lane:
            target = _name("lane", a.lane)
        elif len(d["holders"]) == 1:
            target = d["holders"][0]["lane"]
        else:
            raise Usage("several holders: name the one to release with --lane")
        h = next((x for x in d["holders"] if x.get("lane") == target), None)
        if h is None:
            raise Refused(f"{resource}: {target} does not hold it")
        if who != "owner" and who != h.get("releaser", "holder"):
            raise Refused(f"{resource}: the releaser is {h.get('releaser', 'holder')}, not {who}")
        d["holders"] = [x for x in d["holders"] if x is not h]
        if a.hold:
            d["parked"] = [p for p in d["parked"] if p.get("lane") != target] + [
                {k: h.get(k) for k in ("lane", "ref", "units", "releaser", "reason") if k in h}]
        save(root, d)
    return [f"released {resource} from {target}" + (" (terms kept for the resume)" if a.hold else "")]


def sweep(root: Path) -> list[str]:
    out = []
    for resource in resources(root):
        with locked(str(_path(root, resource))):
            d = load(root, resource)
            gone = _free_stale(d)
            if gone:
                save(root, d)
        out += [f"freed {resource}: {h.get('lane')} ({why})" for h, why in gone]
    return out


def expire(root: Path, lane: str) -> list[str]:
    """The holder's own expiry (heartbeat.sh): free this lane's expired leases, one RELEASE line each."""
    out = []
    for resource in resources(root):
        with locked(str(_path(root, resource))):
            d = load(root, resource)
            mine = [h for h in d["holders"] if h.get("lane") == lane and expired(h)]
            if mine:
                d["holders"] = [h for h in d["holders"] if h not in mine]
                save(root, d)
                out.append(f'RELEASE {resource} reason="lease expired"')
    return out


def status(root: Path, resource: str | None) -> dict:
    names = [_name("resource", resource)] if resource else resources(root)
    leases = []
    for n in names:
        d = load(root, n)
        if d["holders"] or d["parked"]:
            leases.append({**d, "holders": [{**h, "state": stale(h) or "live"} for h in d["holders"]]})
    return {"leases": leases, "stops": [{"scope": s, **v} for s, v in sorted(stops(root).items())]}


# ── STOP files ───────────────────────────────────────────────────────────────────────────────────────────────

def stops(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        files = sorted(stop_dir(root).glob("STOP-*"))
    except OSError:
        return out
    for p in files:
        if not p.is_file():
            continue
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            v = {}
        out[p.name[len("STOP-"):]] = v if isinstance(v, dict) else {}
    return out


def stop(root: Path, scope: str, reason: str, who: str) -> list[str]:
    scope = _name("scope", scope)
    p = stop_dir(root) / f"STOP-{scope}"
    if p.exists():
        cur = stops(root).get(scope, {})
        if cur.get("by") == "owner" and who != "owner":
            raise Refused(f"STOP-{scope} is the owner's; only the owner may rewrite it")
    atomic_write(str(p), json.dumps({"scope": scope, "reason": reason, "by": who, "at": fmt(now())}, indent=2) + "\n")
    return [f"STOP-{scope} written by {who}: {reason}", IN_FLIGHT_RULE]


def go(root: Path, scope: str, who: str) -> list[str]:
    scope = _name("scope", scope)
    p = stop_dir(root) / f"STOP-{scope}"
    if not p.is_file():
        raise Refused(f"no STOP-{scope}")
    by = stops(root).get(scope, {}).get("by", "orchestrator")
    if who != "owner" and who != by:
        raise Refused(f"STOP-{scope} was set by the {by}; only the {by} or the owner lifts it")
    if by == "owner" and who != "owner":
        raise Refused(f"STOP-{scope} is the owner's; only the owner lifts it")
    p.unlink()
    return [f"STOP-{scope} lifted by {who}"]


# ── the hook's reader (ctx_adapter.py hook pre-tool-use) ─────────────────────────────────────────────────────

def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    out: dict[str, str] = {}
    for line in (m.group(1) if m else "").splitlines():
        k, sep, v = line.partition(":")
        if sep and k and not k[0].isspace():
            out[k.strip()] = v.strip().strip("\"'")
    return out


def session_row(root: Path, session_id: str) -> tuple[str, str]:
    """(lane, epic) of the registry row that stamped `session_id`, or ('', '')."""
    if not session_id:
        return "", ""
    try:
        rows = sorted((root / "sessions").glob("*.md"))
    except OSError:
        return "", ""
    for p in rows:
        if p.name.startswith("_"):
            continue
        try:
            fm = _frontmatter(p.read_text(encoding="utf-8", errors="replace")[:4000])
        except OSError:
            continue
        if fm.get("session_id") == session_id and fm.get("status") != "ended":
            return p.stem, fm.get("epic", "")
    return "", ""


def _epic_key(epic: str) -> str:
    return epic.strip().rsplit("#", 1)[-1].strip()


def lane_holds(root: Path, lane: str, scope: str) -> bool:
    if not lane or not NAME_RE.match(scope):
        return False
    try:
        d = load(root, scope)
    except Usage:
        return False
    return any(h.get("lane") == lane and not stale(h) for h in d["holders"])


def stop_for(root: Path, session_id: str) -> str:
    """The reason a STOP file blocks this session's next work-tool call, or ''. A lane is blocked by `STOP-<scope>`
    when it holds the lease `<scope>` or its registry row's `epic` is `<scope>` (the issue number or the full ref)."""
    found = stops(root)
    if not found:
        return ""
    lane, epic = session_row(root, session_id)
    if not lane:
        return ""
    for scope, v in found.items():
        if lane_holds(root, lane, scope) or (epic and _epic_key(epic) == scope):
            why = v.get("reason") or "no reason given"
            return (f"STOP-{scope} ({v.get('by', 'orchestrator')}): {why}. Work tools are blocked until it is lifted: "
                    f"send the outbox line, run the inbox drain, ack, release your lease and end the task, then report "
                    f"what ran (BLOCKED <ticket> stop <who> ran=\"...\").")
    return ""


def exempt_call(tool_name: str, tool_input: object) -> bool:
    """True for the calls docs/swarm.md lets through a STOP: the outbox send, the inbox drain, the ack, the lease
    release and ending the task. A shell command counts only when it is one plain command: any operator, redirect,
    substitution or newline outside what shlex reads as one word makes it a work call."""
    if tool_name in EXEMPT_TOOLS:
        return True
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool_name != "Bash" or not isinstance(cmd, str) or "\n" in cmd or "`" in cmd or "$(" in cmd:
        return False
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return False
    if any(t and all(c in "();<>|&" for c in t) for t in toks):
        return False
    while toks and (re.match(r"^[A-Za-z_]\w*=", toks[0]) or os.path.basename(toks[0]) in ("python", "python3")):
        toks.pop(0)
    if len(toks) < 2:
        return False
    verbs = EXEMPT_SCRIPTS.get(os.path.basename(toks[0]))
    return bool(verbs) and toks[1] in verbs


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lease.py", description=__doc__.split("\n\n")[0] + "\n\nSee the module docstring.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("acquire", help="take a lease on a resource, or refuse naming the holder")
    s.add_argument("resource")
    s.add_argument("--lane")
    s.add_argument("--ref", help="the ticket or ref the lease is for")
    s.add_argument("--pid", type=int, help="the holder's pid (default: the owning claude process)")
    s.add_argument("--minutes", type=int, help="the TTL (default swarm.lease_minutes, else 120)")
    s.add_argument("--releaser", choices=RELEASERS)
    s.add_argument("--reason")
    s.add_argument("--units", type=int, help="units reserved on a resource with a capacity")
    s.add_argument("--capacity", type=int, help="the resource's units, when swarm.units does not name it")
    s = sub.add_parser("release", help="give a lease back (the holder, the named releaser or the owner)")
    s.add_argument("resource")
    s.add_argument("--lane", help="as holder: your lane (default the registered session); else whose lease (default the sole holder's)")
    s.add_argument("--as", dest="as_", choices=RELEASERS, default="holder")
    s.add_argument("--hold", action="store_true", help="a HOLD: keep the terms for the resume")
    s.add_argument("--reason")
    s = sub.add_parser("status", help="leases and STOP files")
    s.add_argument("resource", nargs="?")
    s.add_argument("--json", action="store_true")
    sub.add_parser("sweep", help="free every expired or dead-pid lease")
    s = sub.add_parser("expire", help="free one lane's expired leases, one RELEASE line each")
    s.add_argument("--lane", required=True)
    s = sub.add_parser("stop", help="write STOP-<scope>: the hook blocks the lane that holds it or works that epic")
    s.add_argument("scope")
    s.add_argument("--reason", required=True)
    s.add_argument("--as", dest="as_", choices=STOP_SETTERS, default="orchestrator")
    s = sub.add_parser("go", help="lift STOP-<scope>")
    s.add_argument("scope")
    s.add_argument("--as", dest="as_", choices=STOP_SETTERS, default="orchestrator")
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    root = kit_profile.context_root()
    try:
        if a.cmd == "acquire":
            lines = acquire(root, a)
        elif a.cmd == "release":
            lines = release(root, a)
        elif a.cmd == "sweep":
            lines = sweep(root)
        elif a.cmd == "expire":
            lines = expire(root, _name("lane", a.lane))
        elif a.cmd == "stop":
            lines = stop(root, a.scope, a.reason, a.as_)
        elif a.cmd == "go":
            lines = go(root, a.scope, a.as_)
        else:
            st = status(root, a.resource)
            if a.json:
                lines = [json.dumps(st, indent=2, sort_keys=True)]
            else:
                lines = [f"{d['resource']}: {h['lane']} ref {h.get('ref') or '-'} pid {h.get('pid')} until "
                         f"{h.get('expires')} [{h['state']}] releaser {h.get('releaser')}"
                         + (f" units {h['units']}/{d['capacity']}" if "units" in h else "")
                         for d in st["leases"] for h in d["holders"]]
                lines += [f"STOP-{s['scope']} by {s.get('by', '?')}: {s.get('reason', '')}" for s in st["stops"]]
                lines = lines or ["no leases, no STOP files"]
    except Refused as e:
        print(f"lease: {e}", file=sys.stderr)
        return 1
    except (Usage, OSError) as e:
        print(f"lease: {e}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
