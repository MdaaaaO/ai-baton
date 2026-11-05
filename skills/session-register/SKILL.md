---
name: session-register
description: "Register this session in the live registry (`.context/SESSION_INDEX.md`), narrate back when paste-started as a successor, re-arm PR-watch, rename, keep heartbeat/stats fresh. Use when starting a session on an epic/feature, when responsibilities change, on every flush, and before ending."
metadata:
  version: "44"
  updated: "2026-11-05"
  reviewed: "2026-11-05"
user-invocable: true
---

# session-register — who is working on what, right now

There are **no "master" sessions**: several sessions may work the same epic/feature at once and coordinate through
the **live session registry** in `.context/`. Each session self-describes what it is doing and reads the others'
entries before touching a shared epic, PR or worktree.

- **Write layer:** each session owns exactly one file, `.context/sessions/<name>.md`, and writes *only* that file —
  so sessions never collide (a worktree isolates branch/HEAD, not concurrent directory access).
- **Read layer:** `.context/SESSION_INDEX.md` is generated from all of them. Never hand-edit the index or another
  session's file.

## 1. On startup — orient, then register

1. **Read `.context/SESSION_INDEX.md` first.** When `.context/` is its own git repository with a remote, the
   SessionStart hook has already pulled it (`context sync: pulled …`); a `context sync: conflict pending …` line
   names a doc a person must resolve before anything else is written (`docs/context-sync.md`). See which sessions
   are active, what each owns, and what it says to coordinate on. A `⚠ STALE` row (no heartbeat >12h) may be a
   dead session — re-verify with `ListAgents` (match by **ref**, not name; the tool exists in the main session
   only — a forked worker reports the row to its caller instead) before trusting or messaging it.
   **If you were paste-started from a starter in the Ended table** ("Register as the successor of `<name>`; your
   prompt is in `<path>` § Next session."), the line is not the prompt: open `<path>` (relative to the workspace
   root; it may have moved under `sessions/archive/` — `reference/lifecycle.md` § Successor starters) and read
   its `## Next session` section. Register under the name it proposes, with `SUCCESSOR_OF=<name>` so the
   predecessor's open PRs (`sessions/<name>/prs.tsv`) print, read what it lists, claim what it names.
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
3. **Register yourself.** `NAME` is **`<lane>-<topic>[-n]`**: lower-case kebab-case, ≥ 2 parts, ≤ 32 characters;
   *lane* = the repo or area, *topic* = what you own (a successor on the same lane adds `-2`, `-3`). The name is
   public (it ends every PR body and comment on your own PRs), so no person, org or private project in it — a new
   name embedding one of `tracker.repos`, a domain name or the `tracker.key_regex` shape is refused, and so is a
   placeholder topic (`<lane>-lane-<n>`). Rules in full: `reference/lifecycle.md` § Naming. Get your `ref` from
   `ListAgents` (your own row):

   ```sh
   make -C $BATON/context-db session-register NAME=<name> REF=<ref> EPIC=<tracker-key> \
        REPOS=<repo[,repo]> WORKING="<current ticket/PR in one line>" \
        RESP="<what you own; what others should coordinate with you on>"
   ```
   A successor adds `SUCCESSOR_OF=<predecessor>`: registration then prints `predecessor <name> open PRs:` from that
   session's `sessions/<name>/prs.tsv` (the prose `## Open PRs` only as a fallback; `none` when empty) - the list
   step 4 re-arms or drives - and, for an orchestrator, a `live assignments` block from the swarm ledger. It also
   ends that predecessor's row (`superseded by <name>`) when the row is `active`, has no heartbeat for over 12
   hours and its owner process is gone, and prints `owns: <tickets, PRs>` for it; pass `REF_GONE=1` only after
   `ListAgents` shows the predecessor's ref is not live (it skips the 12 h wait, never the owner-process check).
   Any other predecessor is left as is and a line says why (`reference/lifecycle.md` § End and archiving).
   **A successor orchestrator re-briefs.** For each worker in the `live assignments` block send one
   `ASSIGN <ticket> rebrief=1` (SendMessage by the worker's ref) and wait for its ack. A worker whose registry
   heartbeat is under 5 minutes old is live (re-brief once); an older heartbeat means it ended with no return:
   `swarm.py resume` classifies its row (drive-to-merge when a PR is open, else respawn in place or reassign), no
   re-brief. No registry row: `ListAgents` decides. Standby, retired and ended workers are skipped.
   **A resume re-confirms.** Re-registering a live row without `WORKING=` whose `working_on` was not touched within
   12 hours marks it `unconfirmed: <old>` and prints a line asking for `WORKING=`: restate the current work, or
   `session-touch` it, before trusting it; never keep a stale `working_on` silently.
   **One process per session id.** A register of the same session id under a new `ref` while the row's heartbeat is
   under 5 minutes old prints `DUPLICATE REGISTER` naming both refs: the second process stands down (tell the
   owner, do no work) and leaves the row to the first.
   A parked session registers `STATE=standby`; the row carries `expires` (`swarm.standby_hours`, default 24 h from
   the last heartbeat) and the zombie sweep's `--kill` mode retires it past that (`session.py retire`).
   `make -C $BATON/context-db mission-build` rebuilds the private mission-control page from the registry (a local
   file, zero model turns, never a publish).
   Then fill the body of `.context/sessions/<name>.md` (a direct `Edit`, or the ctx tools naming your actor —
   below) with anything another session needs (what you own vs. don't, in-flight worktrees/PRs). **Override it
   whenever your responsibilities change** — re-run `session-register` (it upserts, preserving the body) or edit
   the body. **Rename** a session registered under a generic or wrong name instead of re-registering fresh — only
   your own live session (`reference/lifecycle.md` § Rename):
   ```sh
   make -C $BATON/context-db session-rename FROM=<old-name> TO=<new-name>
   ```
4. **Re-arm the PR watches.** `Monitor`s die with the session that armed them. For **every open PR you now own**
   (the predecessor's `sessions/<name>/prs.tsv` (`sessions/archive/<name>/prs.tsv` once swept; the prose
   `## Open PRs` of `sessions/<name>.md` only where no tsv exists) - the context doc's *What was built* / *Remaining work*, or `gh pr list --author @me`;
   a predecessor with neither a `prs.tsv` nor an `## Open PRs` heading means unknown, not "none" — check `gh`), verify the
   head with `gh api repos/<o>/<r>/pulls/<n> --jq .head.sha` and arm **one** multi-PR `pr-watch` Monitor per repo
   covering all of them (`PR_WATCH_WORKTREE=<checkout> bash …/pr-watch.sh <o>/<r> <n> <head> [<n> <head> …]`,
   `timeout_ms: 1800000` — the harness cap; each expiry is a billed wake-up).
   **Carve-out:** a PR this session drives to merge itself with `skills/pr-watch/pr-merge.sh` (it counts as that
   PR's watch) needs no Monitor; record it as `driving to merge, no watcher` in `prs.tsv`'s state column. If `pr-merge.sh`
   exits non-zero, arm the Monitor (or park) and rewrite that row. One watcher per PR (a Monitor or
   that `pr-merge.sh` run), never two, never none.
   On expiry, re-arm with the identical call — unless the last **two** windows brought zero actionable events,
   the user signed off, or the only pending event is a human approval / host-only gate: then park
   (`session-handoff` and end the session; `pr-watch` § Park when the gates are not yours).
   Skip a PR only if `SESSION_INDEX.md` shows another *active* session already watching it. Then list the armed
   PRs in `sessions/<name>/prs.tsv` (`repo\tpr\thead7\tstate\tticket`; a swarm orchestrator's `swarm.py` writes it,
   anyone else appends the row). Registration is not complete until the watches are up.

Registering records the name: `python3 $BATON/context-db/bin/kit_profile.py session-name` prints it, and the
session self-identifier — `session \`<name>\`` — is the `footer:` line of the resolved-profile block the
SessionStart hook prints (from the next start; after a compaction drops that block, or after a register/rename
since the start, `kit_profile.py footer` prints the current one). It is the last line of every PR body and PR
comment on your own PRs (`pr-open`, `pr-watch`); never an AI attribution line. The same hook's `compact` matcher
re-grounds you after a compaction with your session file, context doc and session brief.

**Name every ctx write as yourself.** The ctx MCP server writes as one shared process identity unless a call says
otherwise, so a ctx write on a doc your session owns (your `sessions/<name>.md`) without `actor` is refused
`NOT_OWNER`. Pass `actor: <name>` on **every** `ctx_*` write tool call (`ctx_create`, `ctx_str_replace`,
`ctx_insert`, `ctx_delete`, `ctx_rename`, `ctx_log`, `ctx_fm`, `ctx_new`, `ctx_move`, `ctx_maintain`,
`ctx_migrate`) — a write that leaves it out is unnamed, never "this session" by default. The Bash fallback
(`ctx_adapter.py ctx <verb> …`) reads your registered name itself and sets `CTX_ACTOR` from it.

## 2. Keep the heartbeat fresh (≤12h)

Your entry must show a heartbeat within the last 12h or it is treated as stale.

- **Every write under `.context/` touches your row** (the ctx-store `PostToolUse` hook), and then commits and
  pushes the store when it is its own git repository (`docs/context-sync.md`); every `session-register` /
  `session-touch` / `session-end` prints that pass's one line (`pushed …`, `push pending …`, or a `conflict:` to
  resolve). When your focus changes, record it:
  ```sh
  make -C $BATON/context-db session-touch NAME=<name> WORKING="<what you're on now>"
  ```
- **Backstop heartbeat — pure shell, zero model turns.** Right after registering, run once, from the session's
  own Bash tool (not a subagent — it captures `$CLAUDE_CODE_SESSION_ID`, which the stats line needs):
  ```sh
  bash $BATON/skills/session-register/heartbeat.sh <name> "<current focus>"
  ```
  It detaches, touches your row every 6h while the owning `claude` process lives, and marks the row `ended`
  within a minute of the session dying; a second start for the same session is a no-op. Never replace it with a
  `/loop` (one full-prefix turn per tick). Internals: `reference/lifecycle.md` § Backstop heartbeat.

## 2b. Stats — the row shows what the session costs and does

Every `session-register` / `session-touch` / `session-end` refreshes the row's `stats:` line from the session
transcript — **zero model turns**. Spend is the **TOTAL** (main session + every subagent), the same figure the
ledger and `cost-report` use. `make -C $BATON/context-db session-stats` prints the full block `session-handoff`
pastes. Field list and knobs: `reference/lifecycle.md` § Stats.

When the line ends with `split hint: …` (a sustained fat prefix over the last window, never one busy turn):
finish the current step, run `session-handoff`, and continue in a successor session — one scope per session
(`WORKSPACE.md` § Cost & context hygiene).

## 3. On end (part of `session-handoff`)

First refresh `sessions/<name>/prs.tsv` (repo, pr, current head, state, ticket; an orchestrator also leaves its
ledger current) - your `Monitor`s stop with you, and that list is what the successor's step 4 re-arms from. Then:

```sh
make -C $BATON/context-db session-end NAME=<name> NEXT=$(python3 $BATON/context-db/bin/kit_profile.py scratch)/next.md
```
Marks the row `ended`, stores the `NEXT` file as your `## Next session` prompt (`session-handoff` step 10 decides
whether there is one; nothing to hand over → omit `NEXT`; `NEXT=none` withdraws a stale one), writes
`## Session stats` and appends the `_ledger.md` row. When an ended session leaves the index, and how a re-register
reactivates it: `reference/lifecycle.md` § End and archiving. Then do the rest of the `session-handoff` close-out.
A row that says `ended` while its claude process still runs (a pane never closed after the handoff) is a zombie:
`bash $BATON/skills/session-register/zombie-sweep.sh` lists them, `--kill` ends them (`reference/lifecycle.md`
§ Zombie sweep; `kit-health` § 4 reports them).

Row fields: `reference/lifecycle.md` § Fields — operational state, **not** knowledge (excluded from
`make index`/`make verify` and the doc DB count).
