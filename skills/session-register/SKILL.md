---
name: session-register
description: "Register this session in the live registry (`.context/SESSION_INDEX.md`), narrate-back-then-carousel when paste-started as a successor, re-arm PR-watch at startup, rename a mis-named session, name ctx writes as yourself, keep heartbeat/stats fresh. Use when starting a session on an epic/feature, when responsibilities change, on every flush, and before ending."
metadata:
  version: "30"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
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
   for the full text; the table never carries the prompt's own words. A session with nothing to hand
   over is not listed at all. Register under the name it proposes, read what it lists, claim what it
   names. Starter-eligibility window and the archived/no-starter cases: `reference/registry.md` § Startup.
   **Narrate back before touching anything else.** Right after reading `## Next session`, say the
   state back in five lines — Owns · Landed · Open · First step · Not known — before any edit,
   comment, worktree or push (only registration, the heartbeat and re-arming PR watches, steps 3-4
   below, may run first). Check each time-sensitive claim (a PR merged, a draft sent, a ticket
   closed) against the surface before stating it; put anything unverifiable under Not known.
   Confirm with one carousel (`docs/carousel.md`): Proceed (recommended when nothing drifted) ·
   Different first step · Stop — a correction is the carousel's free-text answer. Log the five
   lines — joined with ` · ` — as this session's first Session log entry (`ctx_log` on the
   epic context doc). The headless/no-tool fallback: `reference/registry.md` § Narrate-back.
2. **Decide coordination:** if another *active* session already owns the epic/PR/worktree you
   are about to touch, agree ownership explicitly (one `SendMessage`, or leave it to them) —
   don't both edit the same PR or run git in the same worktree. If no one owns it, you do.
3. **Register yourself.** `NAME` follows **`<lane>-<topic>[-n]`** (kebab-case, ≥2 parts, ≤32 chars) — lane
   = repo/area, topic = what you own; a successor on the same lane adds `-2`/`-3`. Public (it ends every PR
   you post as the `footer:` line, below): the refusal and grandfather rules for the convention:
   `reference/registry.md` § Naming. Get your `ref` from `ListAgents` (your own row):

   ```sh
   make -C $BATON/context-db session-register NAME=<name> REF=<ref> EPIC=<tracker-key> \
        REPOS=<repo[,repo]> WORKING="<current ticket/PR in one line>" \
        RESP="<what you own; what others should coordinate with you on>"
   ```
   Then fill the body of `.context/sessions/<name>.md` (a direct `Edit`, or the ctx tools — `sessions/` is
   exempt from the write deny) with anything another session needs (what you own vs. don't, in-flight
   worktrees/PRs). A ctx write to your own file must name your actor (§ below) or the store refuses it
   `NOT_OWNER`. **Override it whenever your responsibilities change** — re-run `session-register` (it
   upserts, preserving the body) or edit the body directly (`sessions/` stays writable; the hook
   regenerates `SESSION_INDEX.md`).
   **Rename** a session already registered under a generic or wrong name instead of leaving it and
   re-registering fresh — moves the file, restarts your own live backstop heartbeat under the new name.
   Mechanics and the own-live-session-only restriction: `reference/registry.md` § Rename.
   ```sh
   make -C $BATON/context-db session-rename FROM=<old-name> TO=<new-name>
   ```
   `<new-name>` follows the same convention and is refused if another entry already has it.
4. **Re-arm the PR watches.** `Monitor`s die with the session that armed them, so a restarted or
   successor session owns PRs that nobody is watching. A missing `## Open PRs` heading is unknown, not
   "no PRs" — check `gh pr list --author @me` (detail: `reference/registry.md` § Re-arm). For **every
   open PR you now own** (predecessor's `## Open PRs`, the context doc's *Remaining work*, or `gh pr
   list --author @me`), verify the current head with `gh api repos/<o>/<r>/pulls/<n> --jq .head.sha`
   and arm **one** multi-PR `pr-watch` Monitor covering all of them:
   `PR_WATCH_WORKTREE=<checkout> bash …/pr-watch.sh <o>/<r> <n> <head> [<n> <head> …]` under
   `Monitor`, `timeout_ms: 1800000` (30-min cap,
   each expiry billed; one Monitor per repo, not per PR). On expiry, re-arm with the identical call
   unless the last **two** windows brought zero actionable events — then park instead (`session-handoff`
   and end; `pr-watch` § Park). Park at once, not after two windows, the moment the user signs off or
   the only pending event is a human-approval/host-only gate. Skip a PR only if `SESSION_INDEX.md` shows
   another *active* session already watching it. List the armed PRs under `## Open PRs`
   (`repo#n head — what it waits on`) — registration is not complete until the watches are up.

Registering records the name for this session: `kit_profile.py session-name` prints it, and the session
self-identifier — `session \`<name>\`` — is the `footer:` line of the resolved-profile block the
SessionStart hook prints (`kit_profile.py footer` gets the current one after a compaction or a late
rename). It is the last line of every PR body and PR comment on your own PRs (`pr-open`, `pr-watch`);
never an AI attribution line. The same hook re-grounds you after a compaction too — what it opens with
and in what order: `reference/registry.md` § Compaction re-grounding.

**Name every ctx write as yourself (since v0.6.0, #393).** Pass `actor: <name>` (the name you registered
under) on **every** `ctx_*` write tool call (`ctx_create`, `ctx_str_replace`, `ctx_insert`, `ctx_delete`,
`ctx_rename`, `ctx_log`, `ctx_fm`, `ctx_new`, `ctx_move`, `ctx_maintain`, `ctx_migrate`) — not only the ones
that touch your session file: an omitted actor is refused `NOT_OWNER` on a doc you own. The Bash fallback
(`ctx_adapter.py ctx <verb> …`) needs no such flag — it reads your registered name itself. Full rule and
why: `reference/registry.md` § Actor naming.

## 2. Keep the heartbeat fresh (≤12h)

Your entry must show a heartbeat within the last 12h or it is treated as stale.

- **Every write under `.context/` touches your row** — the ctx-store `PostToolUse` hook, keyed by the harness
  session id `session-register` stamped — so an active session stays fresh without a call. When your
  focus changes, record it:
  ```sh
  make -C $BATON/context-db session-touch NAME=<name> WORKING="<what you're on now>"
  ```
- **Backstop heartbeat — pure shell, zero model turns.** Right after registering, run once, from the
  session's own Bash tool (not a subagent — it captures `$CLAUDE_CODE_SESSION_ID`, which the stats line
  below is derived from):
  ```sh
  bash $BATON/skills/session-register/heartbeat.sh <name> "<current focus>"
  ```
  Detaches itself, touches your row every 6h while the owning process is alive, and marks the row
  `ended` within a minute of the session dying — the registry never lies about liveness and no `/loop`
  wake-up re-bills the prefix for it. A second start for the same session/process is a no-op. Later
  focus changes still go through `session-touch … WORKING=`. Process detection, pidfile/log
  namespacing, the retired `/loop 8h` backstop: `reference/registry.md` § Backstop heartbeat.

## 2b. Stats — the row shows what the session costs and does (since 2026-09-19)

Every `session-register` / `session-touch` / `session-end` (yours or the heartbeat's) refreshes the
row's `stats:` line from the session transcript (engine: `$BATON/context-db/bin/session_stats.py`) —
**zero model turns**:

`<n> turns · <h>h · ctx peak/avg · cache-read · out · ~$ (list price) · compactions · tool calls ·
PRs referenced (gh pr create calls) · tickets referenced (created / comments / transitions) · sign
jobs · Slack drafts`

Field definitions (what counts as a turn, the price table, PRs/tickets scope): `reference/registry.md`
§ Stats fields.

- `make -C $BATON/context-db session-stats` prints the full block (window, prompts, token split,
  top tools, delegation, PR/ticket lists, hand-offs) — what `session-handoff` pastes into the wind-down
  entry. `NOSTATS=1` skips the refresh; `SESSION_ID=<uuid>` derives stats for another session.
- On `session-end` the block is written under `## Session stats` in your session file and one row is
  appended to `.context/sessions/_ledger.md` (cross-session ledger; `_`-prefixed files are skipped by
  the index).
- The line ends with `split hint: …` once the average prefix over the last `SESSION_STATS_SPLIT_WINDOW`
  turns (default 20) reaches `SESSION_STATS_SPLIT_THRESHOLD` tokens (default 150k) — a sustained fat
  prefix, not one busy turn. When it shows: finish the current step, run `session-handoff`, and continue
  in a successor session — one scope per session (`WORKSPACE.md` § Cost & context hygiene).

## 3. On end (part of `session-handoff`)

```sh
make -C $BATON/context-db session-end NAME=<name> NEXT=$(python3 $BATON/context-db/bin/kit_profile.py scratch)/next.md
```
Marks the row `ended`, stores the `NEXT` file as your `## Next session` hand-off prompt (the Ended table
gets a starter that points at it, never the prompt's own words; a session with nothing to hand over
omits `NEXT` entirely, and `NEXT=none` withdraws a prompt already on file that has gone stale). Archive
timing for a prompt-less end, and how a resumed session un-archives: `reference/registry.md` § Archive
timing. `session-end` also writes the `## Session stats` block into your session file and appends the
`_ledger.md` row (§ 2b). Before that, refresh the `## Open PRs` list in your session file (repo#n,
current head, what each waits on) — your `Monitor`s stop with you, and that list is what the
successor's startup step 4 re-arms from. Then do the rest of the `session-handoff` close-out (flush to
the context doc through the ctx tools, priorities).

## Fields

`session` (name) · `session_id` (auto: `$CLAUDE_CODE_SESSION_ID` at register — the key a hook matches on) · `ref` (ListAgents ref — the stable id) · `status` (`active`|`idle`|`ended`)
· `epic` (the initiative's tracker key) · `repos` · `working_on` (one line) · `responsibilities` · `stats` (auto, § 2b)
· `heartbeat` (auto) · `updated` (auto). These are operational state, **not** knowledge — the registry is
excluded from `make index`/`make verify` and the doc DB count.
