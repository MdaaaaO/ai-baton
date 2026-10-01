---
name: session-register
description: "Register this session in the live registry (`.context/SESSION_INDEX.md`) and keep its heartbeat fresh (each refreshes the row's stats line). Invoke at the start of any session working an epic/feature, when responsibilities change, on every flush, and before ending. Read the registry to see which session owns an epic, PR or worktree."
metadata:
  version: "29"
  updated: "2026-10-01"
  reviewed: "2026-09-24"
user-invocable: true
---

# session-register — who is working on what, right now

There are **no single "master" sessions** any more. Several sessions may work the same
epic/feature at once. They coordinate through `.context/` — and specifically through the
**live session registry**: each session self-describes what it is doing, and reads the
others' entries to decide whether it must coordinate before touching a shared epic, PR, or
worktree.

- **Write layer:** each session owns exactly one file, `.context/sessions/<name>.md`, and
  writes *only* that file — so sessions never collide (a shared file that everyone rewrites
  would; a worktree isolates branch/HEAD, not concurrent directory access).
- **Read layer:** `.context/SESSION_INDEX.md` is generated from all of them — the one place
  to look. Never hand-edit either the index or another session's file.

## 1. On startup — orient, then register

1. **Read `.context/SESSION_INDEX.md` first.** See which sessions are active, what epic each
   owns, and what it says to coordinate on. A `⚠ STALE` row (no heartbeat >12h) may be a dead
   session — re-verify with `ListAgents` (match by **ref**, not name; the tool exists in the main
   session only — a forked worker reports the row to its caller instead) before trusting or
   messaging it.
   **If you were paste-started from a starter in the Ended table** — a line such as "Register as
   the successor of `<name>`; your prompt is in `<path>` § Next session." — that line is not the
   prompt itself: open `<path>` (relative to the workspace root, e.g. `.context/sessions/<name>.md`,
   or the archive path when the predecessor was already swept) and read its `## Next session` section
   for the full text; the table never carries the prompt's own words. Only the newest `MAX_ENDED`
   ended sessions get a starter; an older one (still in the fold) is named with a link to the same
   path but no starter — open the file the same way. A session that ended with nothing to hand over
   (no `## Next session`, or a "no successor" note) is not listed at all — there is nothing to pick up.
   A predecessor that ended more than `SESSION_ARCHIVE_DAYS` (7) days ago is no longer in the index —
   it was swept to `sessions/archive/<name>.md` (listed in `sessions/archive/INDEX.md`, prompt intact).
   Register under the name it proposes, read what it lists, claim what it names.
   **Narrate back before touching anything else.** Right after reading `## Next session`, say the
   state back in five lines — Owns · Landed · Open · First step · Not known — before any edit,
   comment, worktree or push (only registration, the heartbeat and re-arming PR watches, steps 3-4
   below, may run first). Check each time-sensitive claim (a PR merged, a draft sent, a ticket
   closed) against the surface before stating it; put anything unverifiable under Not known.
   Confirm with one carousel (`docs/carousel.md`): Proceed (recommended when nothing drifted) ·
   Different first step · Stop — a correction is the carousel's free-text answer. No tool available
   (a headless or scheduled run): print the five lines, log them, and proceed only with the steps
   the handoff prompt marks standing go; the rest waits in a `Decisions` block. Either way, log the
   five lines — joined with ` · ` — as this session's first Session log entry (`ctx_log` on the
   epic context doc).
2. **Decide coordination:** if another *active* session already owns the epic/PR/worktree you
   are about to touch, agree ownership explicitly (one `SendMessage`, or leave it to them) —
   don't both edit the same PR or run git in the same worktree. If no one owns it, you do.
3. **Register yourself.** `NAME` follows the convention **`<lane>-<topic>[-n]`**: lower-case kebab-case, at
   least two parts, at most 32 characters. The *lane* is the repo or area (`kit`, the repo's short name), the *topic*
   what you own (`hardening`, a ticket number, a feature); a successor on the same lane adds `-2`, `-3`
   (`kit-hardening`, `kit-216-changelog`, `<repo>-weekly-2`). `session-register` refuses a new name outside it; a
   successor takes the name its predecessor's prompt proposes. The name ends every PR body and PR comment you post on
   your own PRs (the `footer:` line of the resolved-profile block, below), so it is public: no person, org or private project in it — enforced,
   not just asked: a NEW name is refused when it embeds one of this environment's own `tracker.repos` (full slug
   or bare name)/domain names, or its `tracker.key_regex` shape (lower-case included, e.g. a lane like
   `key-123-topic`); an existing name already on file is grandfathered. A generic
   placeholder still matches the regex and is not exempt — `<lane>-lane-<n>` (e.g. `kit-lane-6`) is not a topic;
   the *topic* part must name what you actually own. Get your `ref` from `ListAgents` (your own row):

   ```sh
   make -C $BATON/context-db session-register NAME=<name> REF=<ref> EPIC=<tracker-key> \
        REPOS=<repo[,repo]> WORKING="<current ticket/PR in one line>" \
        RESP="<what you own; what others should coordinate with you on>"
   ```
   Then fill the body of `.context/sessions/<name>.md` (a direct `Edit`, or the ctx tools — `sessions/` is
   exempt from the write deny) with anything another session needs (what you own vs. don't, in-flight
   worktrees/PRs). The session type is owned (§ below): a ctx write to your own file must name your
   actor, or the store refuses it `NOT_OWNER`. **Override it whenever your
   responsibilities change** — re-run `session-register` (it upserts, preserving the body) or
   edit the body directly (`sessions/` stays writable; the hook regenerates `SESSION_INDEX.md`).
   **Rename** a session already registered under a generic or wrong name instead of leaving it and
   re-registering fresh — this moves the file, rewrites its frontmatter and title, and, only when your
   own backstop heartbeat (below) was actually running for the old name, restarts it under the new name
   with the same focus in one step. Only rename **your own live session** — renaming an ended or someone
   else's entry moves the file but starts no heartbeat (there is none of yours to restart), so do that by
   hand if it still needs one. A loop another session started under the same name is left running:
   ```sh
   make -C $BATON/context-db session-rename FROM=<old-name> TO=<new-name>
   ```
   `<new-name>` follows the same convention and is refused if another entry already has it.
4. **Re-arm the PR watches.** `Monitor`s die with the session that armed them, so a restarted or
   successor session owns PRs that nobody is watching. `session.py end`/`touch` always leave a
   `## Open PRs` heading behind (`none` when there is nothing to list) — a predecessor file missing
   the heading entirely predates that fix and is not the same as "no PRs": treat it as unknown and
   check `gh pr list --author @me` before assuming there is nothing to re-arm. For **every open PR
   you now own** (the `## Open PRs` list in the predecessor's session file, the context doc's *What
   was built* / *Remaining work*, or `gh pr list --author @me`), verify the current head with
   `gh api repos/<o>/<r>/pulls/<n> --jq .head.sha` and arm **one** multi-PR `pr-watch` Monitor covering
   all of them (the `pr-watch` skill; `bash …/pr-watch.sh <o>/<r> <n> <head> [<n> <head> …]` under
   `Monitor`, `timeout_ms: 1800000` — the harness caps a Monitor at 30 min, and each expiry is a
   billed wake-up; one Monitor per repo, not per PR; owner decision, 2026-09-22).
   On expiry, re-arm with the identical call (same command and heads; the watcher stays silent) — unless the last **two** windows brought zero actionable events, in which case
   park instead: `session-handoff` and end the session (`pr-watch` § Park when the gates are not yours).
   Park at once — do not wait for two windows — the moment the user signs off, or when the only pending event
   is a human approval / host-only gate (2026-09-22).
   Skip a PR only if `SESSION_INDEX.md` shows another *active* session already watching it
   (one watcher per PR across sessions). Then list the armed PRs under `## Open PRs` in your session file
   (`repo#n head — what it waits on`) so the next session can repeat this step. Registration is not
   complete until the watches are up — a PR whose review lands unwatched is the failure this step
   prevents (owner decision, 2026-09-18).

Registering records the name for this session: `python3 $BATON/context-db/bin/kit_profile.py session-name` prints it,
and the session self-identifier — `session \`<name>\`` — is the `footer:` line of the resolved-profile block the
SessionStart hook prints (from the next session start; once a compaction drops that block, it never printed, or
you registered or renamed after the start — the block shows the name of that moment — `kit_profile.py footer`
prints the current one). It is the last line of every PR body and PR comment on your own PRs
(`pr-open`, `pr-watch`); never an AI attribution line.

The same hook re-grounds you after a compaction too: its `compact` matcher opens with one line naming your
session file and, when one resolves, its context doc, then your session brief — `session`/`epic`/`working_on`/
`responsibilities` first, `## Open PRs`/`## Open decisions`/`## Assumptions`/`## Owns`/`## Worktrees` ahead of
whatever else you wrote — and the context doc's *Remaining work* head, so a compaction does not cost you either.

**Name every ctx write as yourself (since v0.6.0, #393).** The ctx MCP server writes as one shared process
identity unless a call says otherwise, so a ctx tool call that omits `actor` on a doc your session owns (your own
`sessions/<name>.md`, once registered) is refused `NOT_OWNER`. Pass `actor: <name>` (the name you registered
under) on **every** `ctx_*` write tool call (`ctx_create`, `ctx_str_replace`, `ctx_insert`, `ctx_delete`,
`ctx_rename`, `ctx_log`, `ctx_fm`, `ctx_new`, `ctx_move`, `ctx_maintain`, `ctx_migrate`) — not only the ones that
touch your session file: the store only ever sees the actor a call names, so a write that leaves it out is an
unnamed write, never "this session" by default. The Bash fallback (`ctx_adapter.py ctx <verb> …`, used when the
MCP tools are absent) needs no such flag: it reads your registered name itself (`kit_profile.py session-name`)
and sets `CTX_ACTOR` from it when the environment does not already have one.

## 2. Keep the heartbeat fresh (≤12h)

Your entry must show a heartbeat within the last 12h or it is treated as stale.

- **Every write under `.context/` touches your row** — the ctx-store `PostToolUse` hook, keyed by the harness
  session id `session-register` stamped — so an active session stays fresh without a call. When your
  focus changes, record it:
  ```sh
  make -C $BATON/context-db session-touch NAME=<name> WORKING="<what you're on now>"
  ```
- **Backstop heartbeat — pure shell, zero model turns.** Right after registering, run once:
  ```sh
  bash $BATON/skills/session-register/heartbeat.sh <name> "<current focus>"
  ```
  It finds the `claude` process that owns this session (the walk also accepts a bare version string as
  the comm, the shape the desktop app's own binary reports), detaches itself (`skills/_lib/portable.sh`'s
  `detach` — the Bash tool kills its process group after ~10 min otherwise; `setsid` where the host
  has it, a `python3 os.setsid()` re-exec on macOS/BSD, which ship none), touches your row every 6h
  while that process is alive, and marks the row `ended` within a minute of the session dying. So the
  registry never lies about liveness *and* no `/loop` wake-up re-bills the prefix for a heartbeat (the
  old `/loop 8h` backstop cost one full-prefix turn per tick — retired 2026-09-18). A second start for
  the SAME session is a no-op, also under another key: the same name and the same owning process is one
  loop (pidfile `$TMPDIR/ai-baton-<uid>/heartbeat-<key>.pid`; log
  `…heartbeat-<key>.log` beside it, `<key>` your `$CLAUDE_CODE_SESSION_ID` else your name — namespaced
  per user and per session, never a bare `/tmp/heartbeat-<name>.*` shared by everyone on the host, nor
  one name's pidfile mistaken for another live session's). Later focus changes still go
  through `session-touch … WORKING=` — the script's own first tick only fills a still-blank
  `working_on`, never overwriting one you already registered.
  Run it from the session's own Bash tool (not a subagent): it captures `$CLAUDE_CODE_SESSION_ID`
  there, which is what the stats line (below) is derived from.

## 2b. Stats — the row shows what the session costs and does (since 2026-09-19)

Every `session-register` / `session-touch` / `session-end` (yours or the heartbeat's) refreshes the
row's `stats:` line from the session transcript (`~/.claude/projects/*/<session-id>.jsonl`, found via
`$CLAUDE_CODE_SESSION_ID`; engine: `$BATON/context-db/bin/session_stats.py`) — **zero model turns**:

`<n> turns · <h>h · ctx peak/avg · cache-read · out · ~$ (list price) · compactions · tool calls ·
PRs referenced (gh pr create calls) · tickets referenced (created / comments / transitions) · sign
jobs · Slack drafts`

- Turns are **API requests** (deduped per `requestId`), not transcript lines — a multi-block reply is
  one turn. Spend is a list-price floor at `SESSION_STATS_PRICES` (default Opus-4-class
  `15,18.75,1.5,75` $/Mtok in/cache-write/cache-read/out) for the **TOTAL** — main session + every
  subagent transcript summed in, same figure the ledger and cost-report use; `session-stats`'s full
  block (below) breaks the two back out. PRs/tickets are *referenced in tool inputs* (touched), not "owned".
- `make -C $BATON/context-db session-stats` prints the full block (window, prompts, token split,
  top tools, delegation, PR/ticket lists, hand-offs) — what `session-handoff` pastes into the wind-down
  entry. `NOSTATS=1` skips the refresh; `SESSION_ID=<uuid>` derives stats for another session.
- On `session-end` the block is written under `## Session stats` in your session file and one row is
  appended to `.context/sessions/_ledger.md` (cross-session ledger; `_`-prefixed files are skipped by
  the index).
- The line ends with `split hint: …` once the average prefix over the last `SESSION_STATS_SPLIT_WINDOW`
  turns (default 20) reaches `SESSION_STATS_SPLIT_THRESHOLD` tokens (default 150k) — a sustained fat
  prefix, not one busy turn, and never on the strength of turns from before the last auto-compact. When
  it shows: finish the current step, run `session-handoff`, and continue in a successor session — one
  scope per session (`WORKSPACE.md` § Cost & context hygiene).

## 3. On end (part of `session-handoff`)

```sh
make -C $BATON/context-db session-end NAME=<name> NEXT=$(python3 $BATON/context-db/bin/kit_profile.py scratch)/next.md
```
Marks the row `ended`, stores the `NEXT` file as your `## Next session` hand-off prompt (the Ended table
gets a starter that points at it, never the prompt's own words — `session-handoff` step 10 decides
whether there is one to write — a session with nothing to hand over omits `NEXT` entirely, and
`NEXT=none` withdraws a prompt already on file that has gone stale). An ended session **without** a
real prompt (nothing on file, or a "no successor" note) does not appear in the Ended table at all, and
is archived out of the index entirely after
`SESSION_ARCHIVE_NOPROMPT_HOURS` (48, never more than `ARCHIVE_DAYS`) — that covers both a clean end with
nothing left to do and a crash (`heartbeat.sh` ends a crashed session the same way, without a prompt);
`session-register` / `session-touch` / `session-end` move an archived file back from `sessions/archive/`
before writing (a re-register reactivates it), so a resumed session can still add a hand-off later — a
lane that continues with follow-on work always leaves a prompt. `session-end` also writes the
`## Session stats` block into your session file and appends the `_ledger.md` row (§ 2b). Before that,
refresh the `## Open PRs` list in your session file (repo#n, current head, what each waits on) — your
`Monitor`s stop with you, and that list is what the successor's startup step 4 re-arms from.
Then do the rest of the `session-handoff` close-out (flush to the context doc through the ctx tools, priorities).

## Fields

`session` (name) · `session_id` (auto: `$CLAUDE_CODE_SESSION_ID` at register — the key a hook matches on) · `ref` (ListAgents ref — the stable id) · `status` (`active`|`idle`|`ended`)
· `epic` (the initiative's tracker key) · `repos` · `working_on` (one line) · `responsibilities` · `stats` (auto, § 2b)
· `heartbeat` (auto) · `updated` (auto). These are operational state, **not** knowledge — the registry is
excluded from `make index`/`make verify` and the doc DB count.
