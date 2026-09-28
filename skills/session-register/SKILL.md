---
name: session-register
description: Register this session in the live registry (`.context/SESSION_INDEX.md`) and keep its heartbeat fresh (each refreshes the row's stats line). Invoke at the start of any session working an epic/feature, when responsibilities change, on every flush, and before ending. Read the registry to see which session owns an epic, PR or worktree.
metadata:
  version: "11"
  updated: "2026-09-27"
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
   **If you succeed an ended session on the same lane, start from its prompt:** the
   *Next-session prompt* column shows its first line (or only the session's name in the fold, when
   more than `MAX_ENDED` sessions have ended since); the full prompt is `sessions/<name>.md`
   § Next session. A predecessor that ended more than `SESSION_ARCHIVE_DAYS` (7) days ago is no
   longer in the index — it was swept to `sessions/archive/<name>.md` (listed in
   `sessions/archive/INDEX.md`, prompt intact). Register under the name it proposes, read what it
   lists, claim what it names. Re-verify anything time-sensitive (draft sent?, PR merged?) against the surface first.
2. **Decide coordination:** if another *active* session already owns the epic/PR/worktree you
   are about to touch, agree ownership explicitly (one `SendMessage`, or leave it to them) —
   don't both edit the same PR or run git in the same worktree. If no one owns it, you do.
3. **Register yourself.** `NAME` follows the convention **`<lane>-<topic>[-n]`**: lower-case kebab-case, at
   least two parts, at most 32 characters. The *lane* is the repo or area (`kit`, the repo's short name), the *topic*
   what you own (`hardening`, a ticket number, a feature); a successor on the same lane adds `-2`, `-3`
   (`kit-hardening`, `kit-216-changelog`, `<repo>-weekly-2`). `session-register` refuses a new name outside it; a
   successor takes the name its predecessor's prompt proposes. The name ends every PR body and PR comment you post on
   your own PRs (`kit_profile.py footer`, below), so it is public: no person, org or private project in it. Get your
   `ref` from `ListAgents` (your own row):

   ```sh
   make -C $BATON/context-db session-register NAME=<name> REF=<ref> EPIC=<tracker-key> \
        REPOS=<repo[,repo]> WORKING="<current ticket/PR in one line>" \
        RESP="<what you own; what others should coordinate with you on>"
   ```
   Then open `.context/sessions/<name>.md` and fill the body with anything another session
   needs (what you own vs. don't, in-flight worktrees/PRs). **Override it whenever your
   responsibilities change** — re-run `session-register` (it upserts, preserving the body) or
   edit the body directly and `make -C $BATON/context-db session-index`.
4. **Re-arm the PR watches.** `Monitor`s die with the session that armed them, so a restarted or
   successor session owns PRs that nobody is watching. For **every open PR you now own** (the
   `## Open PRs` list in the predecessor's session file, the context doc's *What was built* /
   *Remaining work*, or `gh pr list --author @me`), verify the current head with
   `gh api repos/<o>/<r>/pulls/<n> --jq .head.sha` and arm **one** multi-PR `pr-watch` Monitor covering
   all of them (the `pr-watch` skill; `bash …/pr-watch.sh <o>/<r> <n> <head> [<n> <head> …]` under
   `Monitor`, persistent, `timeout_ms: 3600000` — one Monitor per repo, not per PR; owner decision, 2026-09-22).
   On expiry, re-arm — unless the last **two** windows brought zero actionable events, in which case
   park instead: `session-handoff` and end the session (`pr-watch` § Park when the gates are not yours).
   Park at once — do not wait for two windows — the moment the user signs off, or when the only pending event
   is a human approval / host-only gate (2026-09-22).
   Skip a PR only if `SESSION_INDEX.md` shows another *active* session already watching it
   (one watcher per PR across sessions). Then list the armed PRs under `## Open PRs` in your session file
   (`repo#n head — what it waits on`) so the next session can repeat this step. Registration is not
   complete until the watches are up — a PR whose review lands unwatched is the failure this step
   prevents (owner decision, 2026-09-18).

Registering records the name for this session: `python3 $BATON/context-db/bin/kit_profile.py session-name` prints it,
and `kit_profile.py footer` prints the attribution line with it — `🤖 Generated with [Claude Code](…) · session
\`<name>\`` — the last line of every PR body and PR comment on your own PRs (`pr-open`, `pr-watch`).

## 2. Keep the heartbeat fresh (≤12h)

Your entry must show a heartbeat within the last 12h or it is treated as stale.

- **On every flush** (per-step, per `CLAUDE.md` § Cost & context hygiene) also run:
  ```sh
  make -C $BATON/context-db session-touch NAME=<name> WORKING="<what you're on now>"
  ```
  Active sessions stay fresh automatically this way.
- **Backstop heartbeat — pure shell, zero model turns.** Right after registering, run once:
  ```sh
  bash $BATON/skills/session-register/heartbeat.sh <name> "<current focus>"
  ```
  It finds the `claude` process that owns this session, detaches itself (`setsid nohup` — the Bash
  tool kills its process group after ~10 min otherwise), touches your row every 6h while that process
  is alive, and marks the row `ended` within a minute of the session dying. So the registry never
  lies about liveness *and* no `/loop` wake-up re-bills the prefix for a heartbeat (the old `/loop 8h`
  backstop cost one full-prefix turn per tick — retired 2026-09-18). A second start is a no-op
  (pidfile `/tmp/heartbeat-<name>.pid`; log `/tmp/heartbeat-<name>.log`). Later focus changes still go
  through `session-touch … WORKING=` on each flush — the script only sets the focus you gave it once.
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
  `15,18.75,1.5,75` $/Mtok in/cache-write/cache-read/out) for the **main session only** — subagents
  are their own transcripts. PRs/tickets are *referenced in tool inputs* (touched), not "owned".
- `make -C $BATON/context-db session-stats` prints the full block (window, prompts, token split,
  top tools, delegation, PR/ticket lists, hand-offs) — what `session-handoff` pastes into the wind-down
  entry. `NOSTATS=1` skips the refresh; `SESSION_ID=<uuid>` derives stats for another session.
- On `session-end` the block is written under `## Session stats` in your session file and one row is
  appended to `.context/sessions/_ledger.md` (cross-session ledger; `_`-prefixed files are skipped by
  the index). The ledger feeds `.context/reference/claude-cost-tracking.md`.

## 3. On end (part of `session-handoff`)

```sh
make -C $BATON/context-db session-end NAME=<name> NEXT=$(python3 $BATON/context-db/bin/kit_profile.py scratch)/next.md
```
Marks the row `ended`, stores the `NEXT` file as your `## Next session` hand-off prompt (its first line
in the index's Ended table; `session-handoff` step 10 says what goes in it — an ended session **without**
one is archived out of the index after `SESSION_ARCHIVE_NOPROMPT_HOURS` (48, never more than `ARCHIVE_DAYS`)
— a crashed session is ended by `heartbeat.sh` without a prompt; `session-register` / `session-touch` /
`session-end` move an archived file back from `sessions/archive/` before writing (a re-register reactivates it),
so a resumed session refines its hand-off at any time — a lane that continues always leaves a prompt), writes the `## Session stats`
block into your session file and appends the `_ledger.md` row (§ 2b). Before that, refresh the
`## Open PRs` list in your session file (repo#n, current head, what each waits on) — your
`Monitor`s stop with you, and that list is what the successor's startup step 4 re-arms from.
Then do the rest of the `session-handoff` close-out (flush to the context doc, priorities, index).

## Fields

`session` (name) · `ref` (ListAgents ref — the stable id) · `status` (`active`|`idle`|`ended`)
· `epic` (the initiative's tracker key) · `repos` · `working_on` (one line) · `responsibilities` · `stats` (auto, § 2b)
· `heartbeat` (auto) · `updated` (auto). These are operational state, **not** knowledge — the registry is
excluded from `make index`/`make verify` and the doc DB count.
