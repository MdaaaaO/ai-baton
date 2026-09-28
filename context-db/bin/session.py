#!/usr/bin/env python3
"""session.py — maintain a session's self-reported entry in the live session registry.

Each active Claude session that works on an epic/feature keeps ONE file at
`<content-root>/sessions/<name>.md` describing what it is doing right now. Every
session writes ONLY its own file, so there is no write contention between sessions
(unlike a single shared file that everyone rewrites). `gen_sessions.py` aggregates
them into `<content-root>/SESSION_INDEX.md` — the one place a session reads to see
who else is active and whether it must coordinate.

Subcommands (each regenerates SESSION_INDEX.md):
  register  create/update this session's entry (upsert; preserves the free-text body — `--note` fills only `## Notes`)
  touch     refresh heartbeat + updated only — the <=12h keep-alive
  end       mark the session ended; writes the `## Session stats` block into the body and
            (with --next FILE) the `## Next session` hand-off prompt — omit --next when the session has
            nothing to hand over, and pass --next none to withdraw a prompt already on file — and
            appends one row to sessions/_ledger.md (the cross-session cost/activity ledger)
  stats     print this session's stats (block) without touching the registry

Every subcommand also refreshes the `stats:` field (one line: turns, context, tokens, rough
spend, PRs/tickets/sign jobs/drafts — see session_stats.py) when the session's transcript is
discoverable via $CLAUDE_CODE_SESSION_ID / --session-id; `--no-stats` skips it. Stats are
derived from the transcript with zero model turns, so the live registry row is always current.

The content root is kit_profile.context_root() (CONTEXT_ROOT, which the Makefile sets
from CONTEXT, else docs/layout.md's "Content root" default). Empty CLI values are treated as "leave
unchanged" so the Makefile can pass every flag unconditionally. Stdlib only.
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_profile as profile  # noqa: E402  — same dir
import frontmatter  # noqa: E402  — the one frontmatter parser
from fsutil import atomic_write, locked  # noqa: E402  — same dir

# Heartbeats are stored in UTC; rendered to the terminal/ledger in the owner's local
# zone so they're easy to eyeball against the wall clock.
LOCAL_TZ, _ = profile.zone()  # WORKSPACE_TZ from settings.local.json env, else the env config's tz_default; UTC (+ one stderr line) when unknown


def local_str(iso_utc: str) -> str:
    try:
        t = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return iso_utc
    return t.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %I:%M %p %Z")

CTX = str(profile.context_root())  # the one content-root resolver
SESS_DIR = os.path.join(CTX, "sessions")

# Frontmatter fields, in emit order.
FIELDS = ["session", "ref", "status", "epic", "repos",
          "working_on", "responsibilities", "stats", "heartbeat", "updated"]
STATS_HEADING = "## Session stats"
NEXT_HEADING = "## Next session"
NOTES_HEADING = "## Notes"
LEDGER = os.path.join(SESS_DIR, "_ledger.md")   # `_` prefix: gen_sessions.py skips it
LEDGER_HEADER = """# Session ledger — one row per ended session (stats for geeks)

> Appended by `session.py end`; figures come from each session's transcript via
> `session_stats.py` (list-price estimate per model; `~$` = total (main+subagents) since
> 2026-09-22 — earlier rows are main-session only). Cross-session cost analysis: `.context/reference/claude-cost-tracking.md`.

| Ended | Session | Epic | Turns | Hours | Ctx peak | Cache-read | Out | ~$ | Compactions | PRs | Tickets | Sign jobs | Drafts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
"""

DEFAULT_BODY = """# Session: {name}

<!-- What this session is doing right now, what it OWNS, and what another session
     must coordinate with it on before touching the same epic / PR / worktree.
     Override this whenever your responsibilities change. -->
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# A session name is a file stem under sessions/ (#132): `--name ../INDEX` must not write outside it.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# The naming convention for a NEW registration (owner decision 2026-09-27): `<lane>-<topic>[-n]` — lower-case
# kebab-case, at least two parts, at most 32 characters; the lane is the repo or area, the topic what the session
# owns, `-2`/`-3` a successor on the same lane. Names registered before the convention keep working.
CONVENTION_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
CONVENTION_MAX = 32


def check_convention(name: str) -> None:
    if not CONVENTION_RE.match(name) or len(name) > CONVENTION_MAX:
        sys.exit(f"session.py: {name!r} does not follow the session naming convention `<lane>-<topic>[-n]` — lower-case "
                 f"kebab-case, at least two parts, at most {CONVENTION_MAX} characters (e.g. `kit-hardening`, "
                 f"`kit-216-changelog`, `kit-weekly-2`); a successor on the same lane adds -2, -3")


def record_name(name: str) -> None:
    """Remember this session's registry name in its own scratch dir, so `kit_profile.py session-name` / `footer`
    can say which session wrote a PR or a comment. Per session (`CLAUDE_CODE_SESSION_ID` keys the scratch dir);
    a failure only costs the footer its session part, so it is reported, never fatal. Only a name that follows the
    convention is recorded: the footer is public, and a grandfathered name was never chosen to be (it gets the bare
    footer until the lane re-registers under a conforming name)."""
    if not CONVENTION_RE.match(name) or len(name) > CONVENTION_MAX:
        return
    try:
        (profile.scratch() / "session-name").write_text(name + "\n", encoding="utf-8")
    except (OSError, profile.ScratchError) as e:
        print(f"session.py: could not record the session name for the PR footer ({e})", file=sys.stderr)


def check_name(name: str) -> str:
    if not NAME_RE.match(name or "") or ".." in name:
        sys.exit(f"session.py: invalid session name {name!r} — letters, digits, '.', '_', '-'; "
                 f"starts with a letter or digit; at most 64 characters")
    return name


def path_for(name: str) -> str:
    path = os.path.join(SESS_DIR, f"{check_name(name)}.md")
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(SESS_DIR):
        sys.exit(f"session.py: {name!r} resolves outside sessions/")
    return path


def one_line(v) -> str:
    """A frontmatter value is one line: a newline in WORKING=/RESP= text would start a forged field."""
    return " ".join(str(v).split("\n")).replace("\r", " ").strip()


def restore_from_archive(name: str) -> str:
    """The live path — moved back from sessions/archive/<name>.md when the sweep (gen_sessions.py) took it.

    A crashed session is ended by heartbeat.sh without a next-session prompt and archived once the grace
    window passes; the resumed session's `session-end … NEXT=` / `session-touch` must still find it, so the
    archive window only ever affects the index, never the hand-off. The next regen re-sweeps it if it still
    qualifies (no prompt supplied), so nothing is kept alive by accident."""
    live = path_for(name)
    if os.path.exists(live):
        return live
    # The sweep suffixes a taken name with the ended date (`<name>-YYYYMMDD[-n].md`), so a reused session
    # name may have several archived files: take the newest by heartbeat, never an older lane's doc.
    adir = os.path.join(SESS_DIR, "archive")
    pat = re.compile(rf"^{re.escape(name)}(-(\d{{8}}|undated)(-\d+)?)?\.md$")  # archive_path's suffixes
    cands = []
    for fn in (os.listdir(adir) if os.path.isdir(adir) else []):
        if pat.match(fn):
            meta, _ = read_doc(os.path.join(adir, fn))
            meta = meta or {}
            # Same fallback as gen_sessions.parse (a doc without the field is its stem — only the unsuffixed
            # file can match), so a lane that merely starts with this name is never revived.
            if (meta.get("session") or os.path.splitext(fn)[0]) == name:
                cands.append((meta.get("heartbeat", ""), fn))
    if cands:
        _, fn = max(cands)
        try:
            os.replace(os.path.join(adir, fn), live)
        except FileNotFoundError:
            return live  # heartbeat.sh and the resumed session restored it in the same second — live is back
        print(f"restored {name}.md from sessions/archive/{fn}")
    return live


def read_doc(path: str):
    """Return (meta_dict, body_str), or (None, None) if the file does not exist. Values come back BARE
    (frontmatter.unquote): `write_doc` quotes a value on the way out whenever it needs to (working_on /
    responsibilities free text often contains ': '), and a value round-tripped through read → write must not
    pick up a second layer of quotes each time."""
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    parts = frontmatter.split(text)
    if parts is None:
        return {}, text
    fm, body = parts
    meta = {k: frontmatter.unquote(v) for k, v in frontmatter.parse_flat(fm).items()}
    return meta, body.lstrip("\n")


def quoted_value(v) -> str:
    """`one_line(v)`, double-quoted when the bare value would not be a valid YAML plain scalar
    (frontmatter.plain_scalar_problem) — every field here goes through this on write, so a `: ` in free text
    (working_on, responsibilities) never produces a frontmatter block a real YAML parser refuses."""
    s = one_line(v)
    return frontmatter.quote(s) if s and frontmatter.plain_scalar_problem(s) else s


def write_doc(path: str, meta: dict, body: str) -> None:
    os.makedirs(SESS_DIR, exist_ok=True)
    lines = ["---"]
    for k in FIELDS:
        lines.append(f"{k}: {quoted_value(meta.get(k, ''))}")
    for k, v in meta.items():  # a key this version does not know (a newer kit, a hand-added note) survives the next touch
        if k not in FIELDS:
            lines.append(f"{k}: {quoted_value(v)}")
    lines.append("---")
    # atomic: the heartbeat and the session write the same file; a reader never sees a torn one
    atomic_write(path, "\n".join(lines) + "\n\n" + body.strip() + "\n")


def _stats(a):
    """(stats_dict|None) for this session; None when disabled or no transcript is found."""
    if getattr(a, "no_stats", False):
        return None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import session_stats
    return session_stats.stats_for(getattr(a, "session_id", "") or None)


def _apply_stats(meta: dict, st) -> None:
    if st is not None:
        import session_stats
        meta["stats"] = session_stats.fmt_line(st)


def _replace_section(body: str, heading: str, content: str) -> str:
    """Replace (or append) a `## heading` section in the free-text body. Headings inside a fenced block (a pasted
    prompt that shows `## …`) are text, not sections: neither the heading nor the section end is matched there. Fences
    count only when they balance: stored free text with an odd number of ``` lines is read as before, fence-blind, so one
    stray fence can never hide the heading and turn a replace into a duplicate section.

    An empty `content` withdraws the section instead of leaving an empty one behind: an existing section is dropped
    entirely, and a missing one is left absent (a no-op) — used to clear a stored next-session prompt that has gone
    stale (`--next none`) rather than writing a blank one the reader would still trip over."""
    lines = body.rstrip("\n").split("\n")
    track = sum(1 for ln in lines if ln.lstrip().startswith("```")) % 2 == 0
    out, i, replaced, fence = [], 0, False, False
    while i < len(lines):
        if track and lines[i].lstrip().startswith("```"):
            fence = not fence
        if not fence and lines[i].strip() == heading:
            replaced = True
            if content:
                out += [heading, "", content, ""]
            i += 1
            inner = False
            while i < len(lines) and (inner or not lines[i].startswith("## ")):
                if track and lines[i].lstrip().startswith("```"):
                    inner = not inner
                i += 1
            continue
        out.append(lines[i]); i += 1
    if not replaced and content:
        out += ["", heading, "", content]
    return "\n".join(out).rstrip("\n") + "\n"


def _ledger_append(meta: dict, st) -> None:
    import session_stats
    k = session_stats._k

    def g(*path, default=0):
        """A stats field by path, `default` when a producer (an older session_stats, a partial dict) left it out."""
        cur = st
        for part in path:
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    row = (f"| {local_str(meta.get('heartbeat',''))} | `{meta.get('session','')}` | {meta.get('epic','') or '-'} | {g('turns')} | "
           f"{g('wall_hours', default=0.0)} | {k(g('context', 'peak'))} | {k(g('tokens', 'cache_read'))} | {k(g('tokens', 'output'))} | "
           f"{g('spend_total_usd_est', default=0.0):.0f} ({g('spend_usd_est', default=0.0):.0f}+{g('subagents_cost', 'spend_usd_est', default=0.0):.0f}) | "
           f"{g('compactions')} | {len(g('prs_touched', default=[]))} | {len(g('tickets_touched', default=[]))} | {g('sign_jobs')} | {g('slack', 'drafts')} |")
    with locked(LEDGER):  # two sessions ending at once must not interleave or both write the header
        new = not os.path.exists(LEDGER)
        with open(LEDGER, "a", encoding="utf-8") as f:
            if new:
                f.write(LEDGER_HEADER)
            f.write(row + "\n")


def cmd_register(a) -> None:
    path = restore_from_archive(a.name)  # a successor re-registering a swept name continues that doc, not a new one
    meta, body = read_doc(path)
    if meta is None:  # a new registration: the name must follow the convention (existing ones are grandfathered)
        check_convention(a.name)
        meta, body = {}, DEFAULT_BODY.format(name=a.name)
    meta["session"] = a.name
    if a.ref:    meta["ref"] = a.ref
    # A session that registers is active by definition: registering over an ended (or restored) doc reactivates
    # it, so the row shows under Active and the later session-end writes its stats + ledger row.
    meta["status"] = a.status or ("active" if meta.get("status") == "ended" else meta.get("status")) or "active"
    if a.epic:   meta["epic"] = a.epic
    if a.repos:  meta["repos"] = a.repos
    if a.working is not None: meta["working_on"] = a.working
    if a.resp:   meta["responsibilities"] = a.resp
    if a.note:   body = _replace_section(body, NOTES_HEADING, a.note)  # never the whole body: `## Open PRs` lives there
    _apply_stats(meta, _stats(a))
    meta["heartbeat"] = now_iso()
    meta["updated"] = today()
    write_doc(path, meta, body)
    record_name(a.name)
    print(f"registered {os.path.relpath(path, CTX)}")


def cmd_touch(a) -> None:
    path = restore_from_archive(a.name)
    meta, body = read_doc(path)
    if meta is None:
        sys.exit(f"no session entry {a.name}.md — run "
                 f"`make -C $BATON/context-db session-register NAME={a.name} …` first")
    meta["session"] = meta.get("session") or a.name  # a field-less doc must not be written back with a blank one
    if a.status:              meta["status"] = a.status
    if a.working is not None: meta["working_on"] = a.working
    nxt = _next_prompt(a)
    if nxt is not None:
        body = _replace_section(body, NEXT_HEADING, nxt)
    _apply_stats(meta, _stats(a))
    meta["heartbeat"] = now_iso()
    meta["updated"] = today()
    write_doc(path, meta, body)
    record_name(meta["session"])
    tail = " (+ next-session prompt)" if nxt else (" (next-session prompt withdrawn)" if nxt is not None else "")
    print(f"touched {os.path.relpath(path, CTX)} @ {local_str(meta['heartbeat'])}" + tail)


def _next_prompt(a) -> str | None:
    """Contents of --next FILE: the paste-ready prompt for the successor session. `--next` left unset means
    "leave whatever is stored alone" (None); the literal value `none` withdraws a stored prompt instead of
    reading a file (returns "") — for a session that ends with nothing to hand over, or to clear an earlier
    prompt that turned out wrong or went stale."""
    f = getattr(a, "next", "") or ""
    if not f:
        return None
    if f.strip().lower() == "none":
        return ""
    with open(f, encoding="utf-8") as fh:
        txt = fh.read().strip("\n")
    if not txt:
        sys.exit(f"--next {f} is empty")
    return txt


def cmd_end(a) -> None:
    path = restore_from_archive(a.name)
    meta, body = read_doc(path)
    if meta is None:
        sys.exit(f"no session entry {a.name}.md")
    meta["session"] = meta.get("session") or a.name
    nxt = _next_prompt(a)
    if a.working is not None:
        meta["working_on"] = a.working
    if nxt is not None:
        body = _replace_section(body, NEXT_HEADING, nxt)
    if meta.get("status") == "ended":
        # Already ended: allow the hand-off fields to be refined, but never a second ledger row.
        print(f"{a.name} already ended — no second ledger row", file=sys.stderr)
        if nxt is not None or a.working is not None:
            meta["updated"] = today()
            write_doc(path, meta, body)
            print(f"updated hand-off fields in {os.path.relpath(path, CTX)}"
                  + (" (next-session prompt withdrawn)" if nxt == "" else ""))
        return
    meta["status"] = "ended"
    meta["heartbeat"] = now_iso()
    meta["updated"] = today()
    st = _stats(a)
    if st is not None:
        import session_stats
        _apply_stats(meta, st)
        body = _replace_section(body, STATS_HEADING, session_stats.fmt_block(st))
    write_doc(path, meta, body)
    if st is not None:
        _ledger_append(meta, st)  # after the doc: a failed doc write must not leave a ledger row for an un-ended session
    tail = " (+ next-session prompt)" if nxt else (" (next-session prompt withdrawn)" if nxt is not None else "")
    print(f"ended {os.path.relpath(path, CTX)}" + (" (+ stats block, ledger row)" if st is not None else " (no transcript found — no stats)")
          + tail)


def cmd_stats(a) -> None:
    st = _stats(a)
    if st is None:
        sys.exit("no stats: set CLAUDE_CODE_SESSION_ID / --session-id to a session whose transcript exists")
    import session_stats
    print(session_stats.fmt_block(st))


def regen() -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gen_sessions
    gen_sessions.main([])  # never session.py's own argv


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("register", "touch", "end", "stats"):
        sp = sub.add_parser(name)
        sp.add_argument("--name", required=(name != "stats"))
        sp.add_argument("--session-id", default="", help="transcript to derive stats from (default $CLAUDE_CODE_SESSION_ID)")
        sp.add_argument("--no-stats", action="store_true", help="do not refresh the stats field")
        sp.add_argument("--ref", default="")
        sp.add_argument("--status", default="")
        sp.add_argument("--epic", default="")
        sp.add_argument("--repos", default="")
        sp.add_argument("--working", default=None)   # None = unchanged; "" allowed to clear
        sp.add_argument("--resp", default="")
        sp.add_argument("--note", default="", help="text for the body's `## Notes` section (replaces that section only)")
        sp.add_argument("--next", default="", help="file holding the paste-ready prompt for the successor session "
                        "(written to '## Next session'); 'none' withdraws a stored prompt instead")
    a = p.parse_args()
    # Empty --working means "leave unchanged" (so a bare keep-alive touch never
    # wipes the current focus). The other optional fields already use truthiness.
    if a.working == "":
        a.working = None
    if a.cmd == "stats":
        cmd_stats(a)
        return 0
    check_name(a.name)
    # one registry lock around read-modify-write: the session and its heartbeat.sh touch the same file
    with locked(os.path.join(SESS_DIR, ".registry")):
        {"register": cmd_register, "touch": cmd_touch, "end": cmd_end}[a.cmd](a)
    regen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
