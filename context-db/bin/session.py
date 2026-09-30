#!/usr/bin/env python3
"""session.py — maintain a session's self-reported entry in the live session registry.

Each active Claude session that works on an epic/feature keeps ONE file at
`<content-root>/sessions/<name>.md` describing what it is doing right now. Every
session writes ONLY its own file, so there is no write contention between sessions
(unlike a single shared file that everyone rewrites). `gen_sessions.py` aggregates
them into `<content-root>/SESSION_INDEX.md` — the one place a session reads to see
who else is active and whether it must coordinate.

Subcommands (each regenerates SESSION_INDEX.md):
  register  create/update this session's entry (upsert; preserves the free-text body — `--note` fills only `## Notes`);
            stamps `session_id` with $CLAUDE_CODE_SESSION_ID when set, so a hook handed that id finds the row
  touch     refresh heartbeat + updated only — the <=12h keep-alive
  end       mark the session ended; writes the `## Session stats` block into the body and
            (with --next FILE) the `## Next session` hand-off prompt — omit --next when the session has
            nothing to hand over, and pass --next none to withdraw a prompt already on file — and
            appends one row to sessions/_ledger.md (the cross-session cost/activity ledger)
  stats     print this session's stats (block) without touching the registry
  rename    --from <old> --to <new>: move sessions/<old>.md to <new>.md, rewrite the `session:`
            frontmatter and the `# Session: <name>` title, re-record the scratch session name, and
            when a heartbeat was running for <old>, restart heartbeat.sh under the new name with the same
            focus (best-effort — a heartbeat outside a live session has nothing to attach to and is reported,
            not fatal); refuses when
            <new> already has an entry or does not follow the session naming convention.

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
import signal
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_profile as profile  # noqa: E402  — same dir
import frontmatter  # noqa: E402  — the one frontmatter parser
from fsutil import atomic_write, locked  # noqa: E402  — same dir

# Heartbeats are stored in UTC; rendered to the terminal/ledger in the owner's local
# zone so they're easy to eyeball against the wall clock.
LOCAL_TZ, _ = profile.zone()  # WORKSPACE_TZ from settings.local.json env, else the env config's tz_default; UTC (+ one stderr line) when unknown

CTX = str(profile.context_root())  # the one content-root resolver
SESS_DIR = os.path.join(CTX, "sessions")

# Frontmatter fields, in emit order.
FIELDS = ["session", "session_id", "ref", "status", "epic", "repos",
          "working_on", "responsibilities", "stats", "heartbeat", "updated"]
STATS_HEADING = "## Session stats"
NEXT_HEADING = "## Next session"
NOTES_HEADING = "## Notes"
OPEN_PRS_HEADING = "## Open PRs"
LEDGER = os.path.join(SESS_DIR, "_ledger.md")   # `_` prefix: gen_sessions.py skips it
LEDGER_HEADER = """# Session ledger — one row per ended session (stats for geeks)

> Appended by `session.py end`; figures come from each session's transcript via
> `session_stats.py` (list-price estimate per model; `~$` = total (main+subagents) since
> 2026-09-22 — earlier rows are main-session only). Cross-session cost analysis: `.context/reference/claude-cost-tracking.md`.

| Ended | Session | Epic | Turns | Hours | Ctx peak | Cache-read | Out | ~$ | Compactions | PRs | Tickets | Sign jobs | Drafts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
"""
# The `ledger` type (types/ledger.json) is a doc like any other — it needs the frontmatter `ctx validate` requires
# on a doc, even though the type declares no required fields of its own. A brand new ledger starts with this block;
# an existing one written before this fix gets it prepended once (idempotent: a ledger that already starts with
# `---` is left alone).
LEDGER_FRONTMATTER = "---\ntitle: Session ledger\ntype: ledger\ndomain: sessions\n---\n\n"

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


def check_no_private_value(name: str) -> None:
    """Refuse a NEW session name (a fresh registration, or a rename's target — never a re-register under a name
    already on file) that embeds this environment's own repo/project name or its tracker key shape: the name is
    public — it ends every PR body and PR comment the session posts (`kit_profile.py footer`) — and a lane like
    `key-123-topic` or `<private-repo>-hardening` is exactly the leak a later audit of `public-text-check`'s
    scope found the convention's own "no private project in it" line only asked for, never enforced. Silent (no store,
    or one that fails to load) rather than fatal: a session must still be able to register before `env-init` has
    run. Existing names are grandfathered — this only runs where the caller has already decided the name is
    NEW."""
    try:
        cfg = profile.load(strict=False)
    except SystemExit:
        return
    if not cfg:
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import leak_shapes  # noqa: E402  — same dir
    tracker = cfg.get("tracker") or {}
    kr = str(tracker.get("key_regex") or "").strip()
    if kr:
        try:
            if re.search(kr, name, re.IGNORECASE):
                sys.exit(f"session.py: {name!r} contains this environment's tracker key shape — a session name is "
                         f"public (the PR footer, `kit_profile.py footer`); pick a name that doesn't embed a tracker key")
        except re.error:
            pass  # kit-verify's finding, not this check's problem
    values = set()
    for repo in tracker.get("repos") or []:
        repo = str(repo).strip()
        if repo:
            values.add(repo)
            values.add(repo.rsplit("/", 1)[-1])
    for dom in cfg.get("domains") or []:
        if dom and str(dom).strip() not in profile.CORE_DOMAINS:
            values.add(str(dom).strip())
    for v in values:
        if len(v) < 4 or v.lower() in leak_shapes.GENERIC or v.lower() in leak_shapes.COMMON_REPO_NAMES:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(v.lower()) + r"(?![a-z0-9])", name.lower()):
            sys.exit(f"session.py: {name!r} embeds this environment's own repo/project name ({v!r}) — a session "
                     f"name is public (the PR footer, `kit_profile.py footer`); pick a name that doesn't name it")


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
    (frontmatter.plain_scalar_problem — covers a `: `, a leading `#` or a ` #` anywhere, a leading indicator
    character) — every field here goes through this on write, so free text (working_on, responsibilities,
    e.g. "fix the PR review comments") that happens to mention an issue reference never produces a
    frontmatter block a real YAML parser refuses or truncates."""
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


def _has_section(body: str, heading: str) -> bool:
    """Whether a `## heading` section exists in the free-text body — fence-blind, same rule as
    `_replace_section` (a heading quoted inside a pasted prompt's fenced block is text, not a section)."""
    lines = body.rstrip("\n").split("\n")
    track = sum(1 for ln in lines if ln.lstrip().startswith("```")) % 2 == 0
    fence = False
    for ln in lines:
        if track and ln.lstrip().startswith("```"):
            fence = not fence
        if not fence and ln.strip() == heading:
            return True
    return False


def _ensure_open_prs_heading(body: str) -> str:
    """Guarantee `## Open PRs` exists in the body — `none` when nothing is filled in — so
    session-register step 4 (a successor's re-arm step) can always find the heading and tell "no open
    PRs" apart from "this session never got to filling it in". Never touches an existing section: only
    a body with no `## Open PRs` heading at all gets one appended."""
    if _has_section(body, OPEN_PRS_HEADING):
        return body
    return _replace_section(body, OPEN_PRS_HEADING, "none")


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

    row = (f"| {profile.local_str(meta.get('heartbeat',''), LOCAL_TZ)} | `{meta.get('session','')}` | {meta.get('epic','') or '-'} | {g('turns')} | "
           f"{g('wall_hours', default=0.0)} | {k(g('context', 'peak'))} | {k(g('tokens', 'cache_read'))} | {k(g('tokens', 'output'))} | "
           f"{g('spend_total_usd_est', default=0.0):.0f} ({g('spend_usd_est', default=0.0):.0f}+{g('subagents_cost', 'spend_usd_est', default=0.0):.0f}) | "
           f"{g('compactions')} | {len(g('prs_touched', default=[]))} | {len(g('tickets_touched', default=[]))} | {g('sign_jobs')} | {g('slack', 'drafts')} |")
    with locked(LEDGER):  # two sessions ending at once must not interleave or both write the header/frontmatter
        if not os.path.exists(LEDGER):
            atomic_write(LEDGER, LEDGER_FRONTMATTER + LEDGER_HEADER + row + "\n")
            return
        with open(LEDGER, encoding="utf-8") as f:
            existing = f.read()
        if not existing.startswith("---"):  # a ledger from before this fix: give it frontmatter once
            atomic_write(LEDGER, LEDGER_FRONTMATTER + existing)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(row + "\n")


def cmd_register(a) -> None:
    path = restore_from_archive(a.name)  # a successor re-registering a swept name continues that doc, not a new one
    meta, body = read_doc(path)
    if meta is None:  # a new registration: the name must follow the convention (existing ones are grandfathered)
        check_convention(a.name)
        check_no_private_value(a.name)
        meta, body = {}, DEFAULT_BODY.format(name=a.name)
    meta["session"] = a.name
    # The harness session id, so a Claude Code hook — handed `session_id` on stdin, never the registry name — can
    # find this row (ctx_adapter.py's heartbeat and compact brief match on it). Only the harness's own variable
    # counts: --session-id / SESSION_ID may name another session's transcript for its stats.
    harness_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if harness_id:
        meta["session_id"] = one_line(harness_id)
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
    body = _ensure_open_prs_heading(body)
    _apply_stats(meta, _stats(a))
    meta["heartbeat"] = now_iso()
    meta["updated"] = today()
    write_doc(path, meta, body)
    record_name(meta["session"])
    tail = " (+ next-session prompt)" if nxt else (" (next-session prompt withdrawn)" if nxt is not None else "")
    print(f"touched {os.path.relpath(path, CTX)} @ {profile.local_str(meta['heartbeat'], LOCAL_TZ)}" + tail)


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
    body = _ensure_open_prs_heading(body)
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


def _rename_title(body: str, old_name: str, new_name: str) -> str:
    """Rewrite the body's own `# Session: <old>` title line (DEFAULT_BODY's heading) to the new name.
    Free text elsewhere in the body is left untouched — a rename moves the registry entry, it is not a
    find-and-replace over whatever the session wrote about itself."""
    pattern = re.compile(rf"^# Session: {re.escape(old_name)}[ \t]*$", re.MULTILINE)
    return pattern.sub(f"# Session: {new_name}", body, count=1)


def _baton_tmp() -> str:
    """The per-user tmp dir heartbeat.sh keeps its pidfile/log under — `${TMPDIR:-/tmp}/ai-baton-<uid>`.
    Kept in sync by hand with heartbeat.sh's own `BATON_TMP` (bash and Python don't share one literal);
    this is deliberately NOT `kit_profile.scratch()`'s `ai-baton-kit-<uid>`, a different directory."""
    return os.path.join(os.environ.get("TMPDIR") or "/tmp", f"ai-baton-{os.getuid()}")


def _kill_heartbeat(name: str) -> bool:
    """Stop a live heartbeat.sh for `name` (SIGTERM on the pid its pidfile names) and remove the
    pidfile. Returns whether one was found running — a stale pidfile (process already gone, e.g. the
    session crashed) is just cleaned up, silently."""
    pidfile = os.path.join(_baton_tmp(), f"heartbeat-{name}.pid")
    alive = False
    try:
        with open(pidfile, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return False
    try:
        os.kill(pid, 0)  # existence check only — no signal sent yet
        alive = True
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        alive = True  # another user's process with our pid — leave it; still report "was running"
    try:
        os.remove(pidfile)
    except FileNotFoundError:
        pass
    return alive


def _restart_heartbeat(old_name: str, new_name: str, working: str) -> bool:
    """Stop the old name's heartbeat (if any) and, only when one was actually running, start a fresh one
    for the new name with the same focus. An ended (or archived, `restore_from_archive`-brought-back)
    session, or someone else's, has no heartbeat of its own to restart — starting one anyway would attach
    heartbeat.sh to the CALLER's `claude` process and `CLAUDE_CODE_SESSION_ID`, so every touch would write
    the caller's transcript stats into the renamed row, and the caller's own session-end would later end
    an entry that was never theirs. Best-effort otherwise: heartbeat.sh needs a `claude`/`node` ancestor
    process to attach to — a bare CLI invocation (no owning session, e.g. this being run outside a Claude
    session) fails that lookup; the failure is reported to stderr and never fails the rename itself."""
    was_running = _kill_heartbeat(old_name)
    if not was_running:
        print(f"session.py: no heartbeat was running for {old_name} — none started for {new_name} either "
              f"(start one by hand if this session should have one)", file=sys.stderr)
        return False
    script = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "skills", "session-register", "heartbeat.sh"))
    if not os.path.isfile(script):
        print(f"session.py: heartbeat.sh not found at {script} — start the new heartbeat by hand", file=sys.stderr)
        return False
    try:
        r = subprocess.run(["bash", script, new_name, working], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"session.py: could not restart the heartbeat for {new_name}: {e} — start it by hand", file=sys.stderr)
        return False
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()
        print(f"session.py: heartbeat restart for {new_name} did not start ({detail or 'no output'})"
              " — the old one was stopped — start it by hand", file=sys.stderr)
        return False
    return True


def cmd_rename(a) -> None:
    old_path = restore_from_archive(a.old)
    meta, body = read_doc(old_path)
    if meta is None:
        sys.exit(f"session.py: no session entry {a.old}.md to rename")
    check_convention(a.new)  # a rename is a first-class registration under the new name
    check_no_private_value(a.new)
    new_path = path_for(a.new)
    if os.path.exists(new_path):
        sys.exit(f"session.py: rename target {a.new!r} already has an entry — pick another name, "
                 f"or end/archive it first")
    working = meta.get("working_on", "")
    meta["session"] = a.new
    body = _rename_title(body, a.old, a.new)
    meta["updated"] = today()
    write_doc(new_path, meta, body)
    try:
        os.remove(old_path)
    except FileNotFoundError:
        pass
    record_name(a.new)
    restarted = _restart_heartbeat(a.old, a.new, working)
    print(f"renamed {os.path.relpath(old_path, CTX)} → {os.path.relpath(new_path, CTX)}"
          + (" (heartbeat restarted)" if restarted else " (heartbeat NOT restarted — see above)"))


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
    rn = sub.add_parser("rename")
    rn.add_argument("--from", dest="old", required=True, help="the session's current registry name")
    rn.add_argument("--to", dest="new", required=True, help="the new name (must follow the naming convention)")
    a = p.parse_args()
    # Empty --working means "leave unchanged" (so a bare keep-alive touch never
    # wipes the current focus). The other optional fields already use truthiness. `rename` has no
    # --working of its own (getattr default keeps it out of the way).
    if getattr(a, "working", None) == "":
        a.working = None
    if a.cmd == "stats":
        cmd_stats(a)
        return 0
    if a.cmd == "rename":
        check_name(a.old)
        # same registry lock as register/touch/end: a rename is a read-modify-write too (moves a file
        # another session's heartbeat.sh or session-touch could be mid-write on)
        with locked(os.path.join(SESS_DIR, ".registry")):
            cmd_rename(a)
        regen()
        return 0
    check_name(a.name)
    # one registry lock around read-modify-write: the session and its heartbeat.sh touch the same file
    with locked(os.path.join(SESS_DIR, ".registry")):
        {"register": cmd_register, "touch": cmd_touch, "end": cmd_end}[a.cmd](a)
    regen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
