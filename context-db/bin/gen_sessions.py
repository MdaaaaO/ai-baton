#!/usr/bin/env python3
"""Generate SESSION_INDEX.md — the live registry of Claude sessions.

Aggregates every `<content-root>/sessions/*.md` (each written by one session about
itself, via session.py) into a single catalog, so any session can see at a glance
which others are active, what epic/feature each owns, and whether it must
coordinate before touching a shared epic, PR, or worktree.

An active entry whose heartbeat is older than STALE_HOURS is flagged ⚠ STALE — the
owning session may be gone; re-verify with `ListAgents` (match by ref) before
trusting or messaging it. Stdlib only; run via `make -C $BATON/context-db session-index`.

Size discipline (#71): WORKSPACE.md tells every session to read this file first, so it is
part of every session's start-up cost. Active/idle rows carry everything (working_on,
responsibilities, stats); ended rows are one short line — name, ended-at, the first line
of the next-session prompt — for the newest MAX_ENDED sessions, older ones by name only.
Ended sessions with no next-session prompt for NOPROMPT_HOURS, or ended more than ARCHIVE_DAYS ago, are swept to
sessions/archive/<name>.md (listed in sessions/archive/INDEX.md) so the live registry stays short
under a fold. The full prompt and stats stay in `sessions/<name>.md` (§ Next session,
§ Session stats) and `sessions/_ledger.md`. `make verify` warns when the file passes
SESSION_INDEX_WARN bytes.

The content root is CONTEXT_ROOT (set by the Makefile from CONTEXT, default the
sibling ../../.context of the engine dir).
"""
from __future__ import annotations
import argparse
import glob
import os
import re
from datetime import datetime, timezone

# Content root: the Makefile passes CONTEXT_ROOT; fall back to the sibling .context/
# of the engine dir (../../.context relative to this bin/) for a direct invocation.
CTX = os.environ.get("CONTEXT_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".context")
)
SESS_DIR = os.path.join(CTX, "sessions")
OUT = os.path.join(CTX, "SESSION_INDEX.md")
STALE_HOURS = 12

import kit_profile as profile  # same dir

# Heartbeats are stored in UTC (12h-staleness math and cross-session sorting stay
# correct across DST); every DISPLAYED timestamp is rendered in the owner's local zone.
LOCAL_TZ, _ = profile.zone()  # WORKSPACE_TZ from settings.local.json env, else the env config's tz_default; UTC (+ one stderr line) when unknown


def local_str(iso_utc: str) -> str:
    """'2026-09-23T18:21:00Z' -> '2026-09-23 02:21 PM EDT'; returns the input unchanged if unparsable."""
    try:
        t = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return iso_utc
    return t.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %I:%M %p %Z")


NEXT_HEADING = "## Next session"
# Ended sessions shown as rows (newest first); the rest are listed by name under a fold.
# Makefile: `make … MAX_ENDED=<n>` (exported as SESSION_INDEX_MAX_ENDED).
try:
    MAX_ENDED = max(0, int(os.environ.get("SESSION_INDEX_MAX_ENDED") or 5))
except ValueError:  # a bad value must not leave the index stale behind a traceback
    MAX_ENDED = 5
PROMPT_PREVIEW = 80  # chars of the next-session prompt shown in an ended row

# Ended sessions leave the live registry for sessions/archive/ when nothing waits on them: no next-session
# prompt (nothing for a successor to pick up), or ended more than ARCHIVE_DAYS ago. Makefile:
# `make … ARCHIVE_DAYS=<n>` (exported as SESSION_ARCHIVE_DAYS); 0 sweeps every ended session; --no-archive skips.
ARCHIVE_DIR = os.path.join(SESS_DIR, "archive")
ARCHIVE_OUT = os.path.join(ARCHIVE_DIR, "INDEX.md")
try:
    ARCHIVE_DAYS = max(0, int(os.environ.get("SESSION_ARCHIVE_DAYS") or 7))
except ValueError:
    ARCHIVE_DAYS = 7
# A session with no next-session prompt is not necessarily done: heartbeat.sh ends a crashed session
# with `session-end` and no NEXT, and the resumed session refines the hand-off afterwards. So a
# prompt-less session keeps a grace window (hours) before it is swept — never longer than ARCHIVE_DAYS.
try:
    NOPROMPT_HOURS = max(0, int(os.environ.get("SESSION_ARCHIVE_NOPROMPT_HOURS") or 48))
except ValueError:
    NOPROMPT_HOURS = 48
NOPROMPT_HOURS = min(NOPROMPT_HOURS, ARCHIVE_DAYS * 24)


def section(body: str, heading: str) -> str:
    """Verbatim text of a `## heading` section of the free-text body ("" if absent)."""
    lines, out, on = body.split("\n"), [], False
    for ln in lines:
        if ln.strip() == heading:
            on = True
            continue
        if on and ln.startswith("## "):
            break
        if on:
            out.append(ln)
    return "\n".join(out).strip("\n")


def parse(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    meta: dict[str, str] = {}
    body = text
    if text.count("---") >= 2:
        _, fm, body = text.split("---", 2)
        for line in fm.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
    meta["_next"] = section(body, NEXT_HEADING)
    meta["_path"] = path
    if not meta.get("session"):  # missing or blank: the file stem names the session
        meta["session"] = os.path.splitext(os.path.basename(path))[0]
    return meta


def age_hours(hb: str):
    try:
        t = datetime.strptime(hb, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def cell(m: dict, key: str) -> str:
    return (m.get(key, "") or "").replace("|", "\\|")


def cost_split(stats: str) -> tuple[str, str]:
    """('~$44', stats without the cost term) — the estimate gets its own column."""
    m = re.search(r"\s*·\s*(~\$[\d.,]+[kK]?)", stats)
    if not m:
        return "-", stats or "-"
    return m.group(1), (stats[:m.start()] + stats[m.end():]).strip(" ·") or "-"


def prompt_preview(m: dict) -> str:
    """First non-empty, non-fence line of the next-session prompt, cut to PROMPT_PREVIEW chars."""
    for ln in m.get("_next", "").split("\n"):
        ln = ln.strip()
        if ln and not ln.startswith("```"):
            ln = ln.replace("|", "\\|")
            return ln if len(ln) <= PROMPT_PREVIEW else ln[:PROMPT_PREVIEW - 1].rstrip() + "…"
    return "-"


def by_heartbeat(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda r: r.get("heartbeat", ""), reverse=True)


def archive_reason(m: dict) -> str | None:
    """Why an ended session leaves the live registry — None while a successor may still need it."""
    a = age_hours(m.get("heartbeat", ""))
    if a is None:
        return None  # unparsable ended-at: never drop a row on a bad value (same stance as MAX_ENDED)
    if not m.get("_next"):
        if a > NOPROMPT_HOURS:
            return f"no next-session prompt, ended {int(a)}h ago (> {NOPROMPT_HOURS}h)"
        return None  # inside the grace window — a resumed session may still refine the hand-off
    if a > ARCHIVE_DAYS * 24:
        return f"ended {int(a // 24)}d ago (> {ARCHIVE_DAYS}d)"
    return None


def archive_path(name: str, m: dict) -> str:
    """sessions/archive/<name>.md, suffixed with the ended date (then -2, -3, …) when the name is taken."""
    cand = os.path.join(ARCHIVE_DIR, f"{name}.md")
    if not os.path.exists(cand):
        return cand
    day = (m.get("heartbeat", "") or "")[:10].replace("-", "") or "undated"
    n = 1
    while True:
        cand = os.path.join(ARCHIVE_DIR, f"{name}-{day}{'' if n == 1 else f'-{n}'}.md")
        if not os.path.exists(cand):
            return cand
        n += 1


def sweep(ended: list[dict], dry_run: bool) -> tuple[list[dict], int]:
    """Move the ended sessions nothing waits on to sessions/archive/; returns (still live, moved count)."""
    keep, moved = [], 0
    for m in ended:
        why = archive_reason(m)
        if why is None:
            keep.append(m)
            continue
        dst = archive_path(cell(m, "session"), m)
        rel = os.path.relpath(dst, CTX)
        if dry_run:
            print(f"would archive {cell(m, 'session')} → {rel} ({why})")
            keep.append(m)
            continue
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        try:
            os.replace(m["_path"], dst)
        except FileNotFoundError:
            continue  # a concurrent regen (heartbeat, another session's flush) already took it
        print(f"archived {cell(m, 'session')} → {rel} ({why})")
        moved += 1
    return keep, moved


def write_archive_index(now: datetime) -> int:
    """sessions/archive/INDEX.md — one line per archived session, newest ended first. Returns the count."""
    if not os.path.isdir(ARCHIVE_DIR):
        return 0
    rows = by_heartbeat([parse(p) for p in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.md")))
                         if not os.path.basename(p).startswith("_") and os.path.basename(p) != "INDEX.md"])
    out = [
        "# `.context/sessions/archive/` — SESSION INDEX ARCHIVE",
        "",
        "> **Generated — do not hand-edit.** Ended sessions that left the live registry (`SESSION_INDEX.md`): "
        f"no next-session prompt for {NOPROMPT_HOURS} h, or ended more than {ARCHIVE_DAYS} days ago. Each row's full file is "
        "`sessions/archive/<file>` (its § Next session, § Session stats); the cross-session ledger stays "
        "`sessions/_ledger.md`. Nothing here is read at session start.",
        "",
        f"_generated {now.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %I:%M %p %Z')} · {len(rows)} archived_",
        "",
    ]
    if rows:
        out.append("| Session | Epic | Ended | Next-session prompt (first line) | File |")
        out.append("|---|---|---|---|---|")
        for m in rows:
            out.append(f"| `{cell(m, 'session')}` | {cell(m, 'epic')} | {local_str(m.get('heartbeat', ''))} "
                       f"| {prompt_preview(m)} | `{os.path.basename(m['_path'])}` |")
    else:
        out.append("_empty_")
    with open(ARCHIVE_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="regenerate SESSION_INDEX.md (and sweep ended sessions to sessions/archive/)")
    ap.add_argument("--dry-run", action="store_true", help="print what the sweep would archive; write nothing")
    ap.add_argument("--no-archive", action="store_true", help="regenerate the index without sweeping")
    a = ap.parse_args(argv)
    # `_*.md` are registry side files (the _ledger.md of ended sessions), not sessions.
    rows = []
    for p in sorted(glob.glob(os.path.join(SESS_DIR, "*.md"))):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            rows.append(parse(p))
        except FileNotFoundError:
            continue  # swept by a concurrent regen between the glob and the read
    active = [m for m in rows if m.get("status") != "ended"]
    ended = by_heartbeat([m for m in rows if m.get("status") == "ended"])
    now = datetime.now(timezone.utc)
    moved = 0
    if not a.no_archive:
        ended, moved = sweep(ended, a.dry_run)
    if a.dry_run:
        print(f"dry run — {len(active)} active, {len(ended)} ended stay in the live registry")
        return 0

    out = [
        "# `.context/` — SESSION INDEX (live registry)",
        "",
        "> **Generated — do not hand-edit.** Rebuilt from `sessions/<name>.md` (one per session, via "
        "`make -C $BATON/context-db session-register|touch|end`). **Read it at the start of any session "
        "that will touch a shared epic, PR, or worktree**, then agree ownership explicitly (a worktree "
        f"isolates branch/HEAD, not directory access). ⚠ **STALE** = no heartbeat for over {STALE_HOURS}h; "
        "re-verify with `ListAgents` (match by ref). **Stats** / **~$ est.** come from the session's "
        "transcript (main session only, list price). Heartbeats are shown in the owner's local zone. "
        "An ended row shows the first line of its **next-session prompt**; the full prompt is "
        "`sessions/<name>.md` § Next session, its stats § Session stats and `sessions/_ledger.md`. "
        f"Ended sessions with no next-session prompt for {NOPROMPT_HOURS} h, or ended more than {ARCHIVE_DAYS} days ago, are swept "
        "to `sessions/archive/` (see `sessions/archive/INDEX.md`).",
        "",
        f"_generated {now.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %I:%M %p %Z')} · {len(active)} active · "
        f"{len(ended)} ended_",
        "",
        "## Active",
        "",
    ]

    if active:
        out.append("| Session | Status | Epic | Working on | Responsibilities | Stats | ~$ est. | Heartbeat |")
        out.append("|---|---|---|---|---|---|---|---|")
        for m in by_heartbeat(active):
            hb = m.get("heartbeat", "")
            a = age_hours(hb)
            flag = " ⚠ STALE" if a is not None and a > STALE_HOURS else ""
            ref = f" `{cell(m, 'ref')}`" if cell(m, "ref") else ""
            cost, stats = cost_split(cell(m, "stats"))
            out.append(
                f"| `{cell(m, 'session')}`{ref} | {cell(m, 'status')}{flag} "
                f"| {cell(m, 'epic')} | {cell(m, 'working_on')} "
                f"| {cell(m, 'responsibilities')} | {stats} | {cost} | {local_str(hb)} |"
            )
    else:
        out.append("_none registered_")
    out.append("")

    if ended:
        recent, older = ended[:MAX_ENDED], ended[MAX_ENDED:]
        out.append(f"## Ended (newest {len(recent)})")
        out.append("")
        out.append("| Session | Epic | Ended | Next-session prompt (first line) |")
        out.append("|---|---|---|---|")
        for m in recent:
            ref = f" `{cell(m, 'ref')}`" if cell(m, "ref") else ""
            out.append(f"| `{cell(m, 'session')}`{ref} | {cell(m, 'epic')} "
                       f"| {local_str(m.get('heartbeat', ''))} | {prompt_preview(m)} |")
        out.append("")
        if older:
            names = ", ".join(f"`{cell(m, 'session')}`" for m in older)
            out.append(f"<details><summary>{len(older)} older ended session(s) — `sessions/<name>.md`</summary>")
            out.append("")
            out.append(names)
            out.append("")
            out.append("</details>")
            out.append("")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    archived = write_archive_index(now)
    tail = f", {moved} archived" if moved else ""
    tail += f" ({archived} in sessions/archive/)" if archived else ""
    print(f"wrote {os.path.relpath(OUT, CTX)} — {len(active)} active, {len(ended)} ended{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
