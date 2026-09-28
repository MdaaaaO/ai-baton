#!/usr/bin/env python3
"""Generate SESSION_INDEX.md — the live registry of Claude sessions.

Aggregates every `<content-root>/sessions/*.md` (each written by one session about
itself, via session.py) into a single catalog, so any session can see at a glance
which others are active, what epic/feature each owns, and whether it must
coordinate before touching a shared epic, PR, or worktree.

An active entry whose heartbeat is older than STALE_HOURS is flagged ⚠ STALE — the
owning session may be gone; re-verify with `ListAgents` (match by ref) before
trusting or messaging it. Stdlib only; run via `make -C $BATON/context-db session-index`.

Size discipline: WORKSPACE.md tells every session to read this file first, so it is
part of every session's start-up cost. Active/idle rows carry everything (working_on,
responsibilities, stats); ended rows are one short line — name, epic, ended-at — for the
newest MAX_ENDED sessions, older ones by name only. Neither carries the prompt's own text
any more (writing a successor's chat into a file every session reads at startup was the
bug this file fixes): a session that left a real next-session prompt gets one starter block
below the table instead, a plain line the successor pastes verbatim — "Register as the
successor of <name>; your prompt is in <path> § Next session." — plus a link whose text is
that same path, for a viewer that shows link text but does not resolve it. Only the newest
MAX_ENDED sessions get a block; older ones (still in the fold) get the path without one. A
session that ended with no real prompt — nothing written, blank, or a "no successor" note
(session-handoff's wording for a lane that intentionally left nothing to hand over) — does
not appear in the Ended table at all; `has_next_prompt()` is the one place that decides "real".
Ended sessions with no next-session prompt for NOPROMPT_HOURS, or ended more than ARCHIVE_DAYS ago, are swept to
sessions/archive/<name>.md (listed in sessions/archive/INDEX.md) so the live registry stays short
under a fold. The full prompt and stats stay in `sessions/<name>.md` (§ Next session,
§ Session stats) and `sessions/_ledger.md`. `make verify` warns when the file passes
SESSION_INDEX_WARN bytes.

The content root is kit_profile.context_root() (CONTEXT_ROOT, which the Makefile sets
from CONTEXT, else docs/layout.md's "Content root" default).
"""
from __future__ import annotations
import argparse
import glob
import os
import re
import sys
from datetime import datetime, timezone

import kit_profile as profile  # same dir — the one content-root resolver
import frontmatter  # same dir — the one frontmatter parser
from fsutil import atomic_write  # same dir
from session_stats import SPEND_BASIS  # same dir — one spend-basis wording, quoted verbatim here too
from session import NEXT_HEADING  # same dir — the writer owns the session doc's section headings

CTX = str(profile.context_root())
SESS_DIR = os.path.join(CTX, "sessions")
OUT = os.path.join(CTX, "SESSION_INDEX.md")
STALE_HOURS = 12

# Heartbeats are stored in UTC (12h-staleness math and cross-session sorting stay
# correct across DST); every DISPLAYED timestamp is rendered in the owner's local zone.
LOCAL_TZ, _ = profile.zone()  # WORKSPACE_TZ from settings.local.json env, else the env config's tz_default; UTC (+ one stderr line) when unknown

# Ended sessions shown as rows (newest first); the rest are listed by name under a fold.
# Makefile: `make … MAX_ENDED=<n>` (exported as SESSION_INDEX_MAX_ENDED).
try:
    MAX_ENDED = max(0, int(os.environ.get("SESSION_INDEX_MAX_ENDED") or 5))
except ValueError:  # a bad value must not leave the index stale behind a traceback
    MAX_ENDED = 5

# A stored next-session prompt that is only a "no successor" note — session-handoff's wording for a lane
# that ended with nothing to hand over but still wanted to point a reader at where the work went (e.g.
# "No successor: the lane folds into <other session>") — tells a reader where the work went, not what to
# paste to pick it up: it does not count as a real prompt, same as an absent or blank one.
NO_SUCCESSOR_RE = re.compile(r"^no\s+(?:successor|follow-?up)\b", re.IGNORECASE)  # the handoff skill's two closing phrases


def has_next_prompt(m: dict) -> bool:
    """Whether `m["_next"]` is a real hand-off — the one place that decides it, so the Ended table, the
    archive sweep grace window and the archive index all agree. Blank, whitespace-only, and a "no successor"
    note are all "no prompt"."""
    text = (m.get("_next", "") or "").strip()
    return bool(text) and not NO_SUCCESSOR_RE.match(text)


def rel_from_root(path: str) -> str:
    """`path` (a session's `_path`, live under sessions/ or already swept to sessions/archive/) relative to
    the workspace root, e.g. `.context/sessions/<name>.md` — the form a fresh session at the workspace root
    can open directly, and what session-register's startup step resolves the prompt from. The prefix is the
    content root's own directory name, so a CONTEXT_ROOT with another basename still yields a real path."""
    return os.path.relpath(path, os.path.dirname(os.path.abspath(CTX))).replace(os.sep, "/")


# Ended sessions leave the live registry for sessions/archive/ when nothing waits on them: no next-session
# prompt (nothing for a successor to pick up), or ended more than ARCHIVE_DAYS ago. Makefile:
# `make … ARCHIVE_DAYS=<n>` (exported as SESSION_ARCHIVE_DAYS); 0 sweeps every ended session; --no-archive skips.
ARCHIVE_DIR = os.path.join(SESS_DIR, "archive")
ARCHIVE_OUT = os.path.join(ARCHIVE_DIR, "INDEX.md")
try:
    ARCHIVE_DAYS = max(0, int(os.environ.get("SESSION_ARCHIVE_DAYS") or 7))
except ValueError:
    ARCHIVE_DAYS = 7
# A session with no next-session prompt is not necessarily done being wrapped up: it may be a crash —
# heartbeat.sh ends a crashed session with `session-end` and no NEXT, and a resumed session refines the
# hand-off afterwards — or it may be a clean end that intentionally left nothing to hand over
# (session-handoff). Either way a prompt-less session keeps a grace window (hours) before it is swept —
# never longer than ARCHIVE_DAYS.
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
    # the shared parser: only a leading `---` block is front matter — a `---` rule in the body is body (#132)
    parts = frontmatter.split(text)
    # bare values: session.py quotes a field on write whenever the raw text needs it (working_on/responsibilities
    # free text often contains ': '), and every reader of this registry wants the plain string back
    meta: dict[str, str] = ({k: frontmatter.unquote(v) for k, v in frontmatter.parse_flat(parts[0]).items()}
                             if parts else {})
    body = parts[1] if parts else text
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


def by_heartbeat(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda r: r.get("heartbeat", ""), reverse=True)


def archive_reason(m: dict) -> str | None:
    """Why an ended session leaves the live registry — None while a successor may still need it."""
    a = age_hours(m.get("heartbeat", ""))
    if a is None:
        return None  # unparsable ended-at: never drop a row on a bad value (same stance as MAX_ENDED)
    if not has_next_prompt(m):
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
        except OSError as e:
            print(f"gen_sessions: could not archive {m['_path']}: {e} — kept in the index", file=sys.stderr)
            keep.append(m)
            continue
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
        f"_generated {now.astimezone(LOCAL_TZ).strftime(profile.LOCAL_TIME_FMT)} · {len(rows)} archived_",
        "",
    ]
    if rows:
        # Same rule as the live table: a link (text = path), never the prompt's own text — "-" when the
        # session left no real prompt to point at (has_next_prompt, shared with archive_reason above).
        out.append("| Session | Epic | Ended | File | Prompt |")
        out.append("|---|---|---|---|---|")
        for m in rows:
            path = rel_from_root(m["_path"])
            prompt = f"[{path}]({path})" if has_next_prompt(m) else "-"
            out.append(f"| `{cell(m, 'session')}` | {cell(m, 'epic')} | {profile.local_str(m.get('heartbeat', ''), LOCAL_TZ)} "
                       f"| `{os.path.basename(m['_path'])}` | {prompt} |")
    else:
        out.append("_empty_")
    atomic_write(ARCHIVE_OUT, "\n".join(out) + "\n")
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
        except (OSError, ValueError) as e:  # unreadable / not UTF-8: name it, never drop it silently
            print(f"gen_sessions: skipped {p}: {e}", file=sys.stderr)
    active = [m for m in rows if m.get("status") != "ended"]
    ended = by_heartbeat([m for m in rows if m.get("status") == "ended"])
    now = datetime.now(timezone.utc)
    moved = 0
    if not a.no_archive:
        ended, moved = sweep(ended, a.dry_run)
    if a.dry_run:
        print(f"dry run — {len(active)} active, {len(ended)} ended stay in the live registry")
        return 0
    # The Ended table lists only a session that left a real hand-off (has_next_prompt); one that ended
    # clean with nothing to hand over, or crashed, still lives in `ended` for the archive grace window
    # (archive_reason) and the sessions/ directory, just not here — there is nothing for a reader to pick up.
    listed = [m for m in ended if has_next_prompt(m)]
    unlisted = len(ended) - len(listed)

    out = [
        "# `.context/` — SESSION INDEX (live registry)",
        "",
        "> **Generated — do not hand-edit.** Rebuilt from `sessions/<name>.md` (one per session, via "
        "`make -C $BATON/context-db session-register|touch|end`). **Read it at the start of any session "
        "that will touch a shared epic, PR, or worktree**, then agree ownership explicitly (a worktree "
        f"isolates branch/HEAD, not directory access). ⚠ **STALE** = no heartbeat for over {STALE_HOURS}h; "
        "re-verify with `ListAgents` (match by ref). **Stats** / **~$ est.** come from the session's "
        f"transcript ({SPEND_BASIS}). Heartbeats are shown in the owner's local zone. "
        "The Ended table lists only a session that left a real **next-session prompt** — a starter below "
        "the table points at it (`sessions/<name>.md` § Next session); a session that ended with nothing "
        "to hand over is not listed here (its file and stats stay in `sessions/<name>.md` § Session stats "
        "and `sessions/_ledger.md`, and it is swept on the usual schedule). "
        f"Ended sessions with no next-session prompt for {NOPROMPT_HOURS} h, or ended more than {ARCHIVE_DAYS} days ago, are swept "
        "to `sessions/archive/` (see `sessions/archive/INDEX.md`).",
        "",
        f"_generated {now.astimezone(LOCAL_TZ).strftime(profile.LOCAL_TIME_FMT)} · {len(active)} active · "
        f"{len(listed)} ended listed · {unlisted} ended with no prompt (not listed)_",
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
                f"| {cell(m, 'responsibilities')} | {stats} | {cost} | {profile.local_str(hb, LOCAL_TZ)} |"
            )
    else:
        out.append("_none registered_")
    out.append("")

    if listed:
        recent, older = listed[:MAX_ENDED], listed[MAX_ENDED:]
        out.append(f"## Ended (newest {len(recent)})")
        out.append("")
        out.append("| Session | Epic | Ended |")
        out.append("|---|---|---|")
        for m in recent:
            ref = f" `{cell(m, 'ref')}`" if cell(m, "ref") else ""
            out.append(f"| `{cell(m, 'session')}`{ref} | {cell(m, 'epic')} "
                       f"| {profile.local_str(m.get('heartbeat', ''), LOCAL_TZ)} |")
        out.append("")
        # One starter per shown session — never in the table itself (a cell cannot hold a fenced block).
        # Paste-ready: no markdown inside the fence, so a successor can copy the whole line as-is.
        for m in recent:
            name = cell(m, "session")
            path = rel_from_root(m["_path"])
            out.append(f"`{name}` — [{path}]({path})")
            out.append("")
            out.append("```text")
            out.append(f"Register as the successor of {name}; your prompt is in {path} § Next session.")
            out.append("```")
            out.append("")
        if older:
            entries = ", ".join(
                f"`{cell(m, 'session')}` ([{rel_from_root(m['_path'])}]({rel_from_root(m['_path'])}))"
                for m in older
            )
            out.append(f"<details><summary>{len(older)} older ended session(s)</summary>")
            out.append("")
            out.append(entries)
            out.append("")
            out.append("</details>")
            out.append("")

    atomic_write(OUT, "\n".join(out) + "\n")
    archived = write_archive_index(now)
    tail = f", {moved} archived" if moved else ""
    tail += f" ({archived} in sessions/archive/)" if archived else ""
    print(f"wrote {os.path.relpath(OUT, CTX)} — {len(active)} active, {len(listed)} ended listed"
          f", {unlisted} with no prompt{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
