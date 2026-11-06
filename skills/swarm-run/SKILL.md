---
name: swarm-run
description: "The swarm orchestrator's tick: assigns an approved epic's tickets, drains returns, integrates and verifies PRs, writes the digest. Use when running or resuming an epic's swarm, or on its tick wake-up. Not for one ticket (`ticket-pickup`) or a PR you own alone (`pr-watch`)."
metadata:
  version: "2"
  updated: "2026-11-05"
  reviewed: "2026-11-05"
user-invocable: true
argument-hint: "<epic key>"
---

# swarm-run — the orchestrator's tick

`$BATON/docs/swarm.md` is the contract: § The swarm tick fixes the order, § Authority matrix who decides. This skill
carries the steps out with the engine; it restates no rule. Every step reads disk, the tracker and git, never memory or
a quoted table. The orchestrator never builds a ticket, not even a hotfix. Scratch: `CTX=$(mktemp -d)`, spelled out in
each call. `E=$BATON/context-db/bin`. Commands per step: `reference/commands.md`; the tick as a picture: `reference/flow.md`.

## 0. Start the session

1. Register on the command line, never by an exported variable, which the target ignores:
   `make -C $BATON/context-db session-register NAME=<name> EPIC=<key> ROLE=orchestrator` (a successor adds
   `SUCCESSOR_OF=<predecessor>`). Without the role `swarm.py tick` reads no orchestrator, an epic cap closes the pool,
   and an epic `STOP` denies this session's own calls.
2. `python3 $E/swarm.py show <key>` must find the ledger. None → `swarm-run: no task ledger for <key> — decompose and
   get the owner's go first`, and stop.
3. A successor sends `ASSIGN <ticket> <file> rebrief=1` to every worker with an open assignment and a live run.
4. Headless (no `AskUserQuestion`): every owner question goes to the `Decisions` block (`docs/carousel.md` § No tool),
   which ends the reply and the digest; the rest of the tick is the same.

## 1. Read

`python3 $E/swarm.py tick <key> --json > $CTX/tick.json` (it also runs the budget sweep). Then the tracker (the epic,
its children, their PRs) and the branches. Ledger and branches disagree → `swarm.py resume <key> --repo-dir <clone>
--json`; decide, or ask, before `--apply`.

## 2. Inbox

Drain your inbox, each worker's outbox and each returned run's final lines (`swarm_msg.py drain`), tracker comments,
PR events, chat items where the machine has chat, and `watches_due`. A `STOP` or `HOLD` acts first. Status lines go
through `swarm.py apply <key> "<line>"`; ack each id (`swarm_msg.py ack --as <your lane>`). `swarm_msg.py pending`:
re-send once with the same id; the next window makes the lane `unreachable` (step 5). Inside `swarm.reopen_minutes`
a miss is a wait, never a loss.

## 3. Decisions

`BLOCKED`: the `<who>` acts; yours → re-brief, reassign, park or ask. `NEEDS` → resolve the fact (`env-init`), then
re-brief. `DECISION` → `swarm.py decision <key> --question … --for-ticket <t>`. A comment that adds scope is a dated
delta on the epic and starts nothing before a go. Batch every open decision into one carousel, only while you hold no
lease and run no child; otherwise, and headless, into the `Decisions` block. The answer closes it with its line
(`--close <id> --line`). A question with no safe default parks only the work behind it.

## 4. Assign

Take only `assignable` from the tick; it is empty past `max_workers`, past the epic budget, before `plan_approved` or
under a `STOP`, and `no_assign` says why: assign nothing and put the reason in the digest. Never assign around it.

- **Probe a peer first:** `ListAgents`, by ref; where the harness has no peer list, the registry row's heartbeat.
- **Per ticket:** `ticket-pickup` for the sizing, the Done-when probe and the sketch (its launch step is replaced by
  this one); a worktree and branch from the default branch; `swarm.py assign --kind …` first, because `caps` prints
  the headless `flag:` line only once the row's kind is set; then `swarm_budget.py caps <key> <ticket>`; the assignment
  file from `$BATON/context-db/_templates/assignment.md` at `.context/state/swarm/<key>/<worker>/assignment.md`, every
  field filled; the `ASSIGN` line into the worker's inbox (`swarm_msg.py format` + `send --inbox`).
- **Spawn all in one message**, in the background, the model the Sizing line names, one worktree each. A headless
  worker takes the caps' `flag:` line (`--max-budget-usd`).
- **An incident lane** is only for breakage in what is merged: `swarm.py add <key> <t> --incident`, a dated re-plan
  delta on the epic, the owner approves after. It pre-empts the queue. You never take the fix yourself.

## 5. Stalls

Only `assigned` and `claimed` rows stall. Per `stalled` entry, by its `next`: `count` → `swarm.py progress <key> <t>
--stall`; `rebrief` → count it and send one `ASSIGN … rebrief=1` with what is missing in `note="…"`;
`reassign-or-park` → a new worker (`swarm.py assign`) or `swarm.py status <key> <t> parked --reason "…"` plus the
`PARKED` line. Each leaves its ledger line. A lost worker (`unreachable`, `⚠ STALE`, ended with no return, lost its
background task) → `swarm.py resume <key> --repo-dir <clone> --json`, and act on each row's `action`: `drive-to-merge`
→ step 7; `respawn-in-place` → resume its run with the note and the findings inline; `reassign` → a new worker;
`record-merge` or `update` → `--apply`; `ask` → step 3. `ASSIGN … rebrief=1` goes only to a live run, one whose
heartbeat is under 5 minutes old; a dead run is respawned, never messaged. **Before you touch a worktree**,
`ps -eo pid,args` for its path: a worker that ended its turn on a background CI is still running, a merge into its
worktree voids that run, and its CI line is a placeholder until the run ends.

## 6. Budget

Read the tick's `budget` and `no_assign`; `swarm_budget.py digest <key>` gives the digest's lines. Past the epic cap
spawn nothing and say so. A hard stop prints `task-stop <ticket> task=<id>`: `TaskStop` that id; never a pid, never a
pattern. A worker's own dollar breach is its `BLOCKED … budget` and leaves the pool open. Only the owner raises a cap.

## 7. Integrate

For each `integrate` entry (a `READY` with no PR) or a peer's `PR` line, in this order (`reference/integrate.md`):

1. **Draft PR** from the worker's body file: `gh pr create -R <owner/repo> --draft --base <default> --head <worker
   branch> --title "<title>" --body-file <file> </dev/null`, the title passing `commit_style.py title`, then
   `swarm.py status <key> <t> pr --pr <n>`. It stays a draft until the review overview, the miss check and the
   verifier's `phase=read` are all back, because a repo's own auto-merge can merge a non-draft PR once its gates hold.
2. **Code owners at PR open:** owners per touched path from the live `CODEOWNERS` and the branch protection. A path
   whose only reachable owner is the author → `needs owner of <path>` in the digest before any `READY` head counts.
   Approved means `reviewDecision: APPROVED`, never one review.
3. **Protected paths:** `swarm.protected_paths` (default `.github/`, `hooks/`, `settings*.json`) plus every path the
   repo's own merge gate reserves for its owner (its auto-merge workflow or rulesets, read at the base). A PR on one is
   never merged by you: the digest says `needs the owner's merge`. Where the repo's auto-merge would still take it,
   stop at `NEEDS owner` before `gh pr ready`: marking it ready arms that merge.
4. **Review:** the `review-runner` overview (`model: sonnet` for a diff of 20 changed lines or fewer, by `git diff
   --shortstat`) and the miss check (`docs/miss-check.md`). Findings → `ASSIGN <t> <file> rebrief=1` with the
   findings inline in `note="…"`, then resume that worker. A clean review needs no wake.
5. **Ready and watch:** with the overview, the miss check and step 8's `phase=read` back and no protected path,
   `gh pr ready`, then arm `pr-watch`. When the only open gate is one known check run, wait with a background
   `gh run watch <id> --exit-status`, never a Monitor window. After a push, cancel the review runs on the old head.
6. **Merge gate**, read from `swarm.merge.<owner/repo>` (`auto | orchestrator | owner`, default `orchestrator`): when
   the gates hold at the `READY` head, `swarm.py status <key> <t> ready-to-merge --head <sha>`. `orchestrator` → one
   standalone `bash $BATON/skills/pr-watch/pr-merge.sh <owner/repo> <pr> <READY sha>` call; `auto` and `owner` →
   watch for it. A moved head, a base update included, needs a new `READY`. Never an admin or bypass merge.
7. **Merged:** `swarm.py status <key> <t> merged --pr <n> --sha <sha> [--merged-by owner|auto]`, `MERGED` into the
   worker's inbox, and `DONE` for a background worker whose run has ended.

## 8. Verify

Each `READY` head before its merge → a fresh `verifier`, `PHASE: read` (`reference/integrate.md` has the line):
`FAIL` → a re-brief; `PASS` opens step 7's gate for that head only, at the next tick. Each merged PR → a fresh
`verifier`, `PHASE: merged`; it alone calls `swarm.py pass`. A `FAIL` → `swarm.py status
<key> <t> blocked --reason "…"`, and the fix is a new assignment on a new branch. All passed → `ticket-close`, then
`swarm.py status <key> <t> done`. After the last child's `phase=merged`: the epic body to `$CTX/plan.md`, `gates.py
status $CTX/plan.md`, a verifier `PHASE: gates` naming only the open agent gates, and the signed file posted back.
Human gates go to the owner (step 3) and `gates.py sign --by owner`. The epic closes only on exit 0: any other exit,
a malformed plan's 2 included, is no close.

## 9. Digest

Every `swarm.digest_hours` (default 4) and at each wave end, on the epic through `ticket-update`: shipped; blocked,
with who must act; `needs owner of <path>` and `needs the owner's merge`; decisions open; spend against caps; stalls;
next. Headless, it ends with the `Decisions` block.

## 10. Flush

`swarm.py render <key>` into the context doc's `## Swarm` section (`ctx_str_replace`), `session-register`'s flush for
the heartbeat, and the next wake-up no later than the next due watch or digest. At a wave boundary, end the session
with `session-handoff` and its next-session prompt: one scope per session.
