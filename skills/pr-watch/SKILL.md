---
name: pr-watch
description: "Low-noise PR watch: one Monitor per repo/session surfaces only actionable events, triages via `pr-event-brief`, keeps branches synced, merges via `pr-merge.sh` once gates hold. Parks on sign-off, idle windows or a human-only gate. Use when your session owns an open PR."
metadata:
  version: "31"
  updated: "2026-10-15"
  reviewed: "2026-10-15"
---

# pr-watch — stay on top of your PRs without the noise

**One `Monitor` per repo per session**, covering every open PR that session owns in that repo (one process
round-robins the PRs, one `sleep 120` per cycle). It prints one line per actionable event; the harness turns each
line into a notification, so polling is free and only real events — plus the Monitor's own 30-min expiry — cost a
wake-up at full prefix (`reference/cost-controls.md`).

## Watches are session-scoped — re-arm on every session start

A `Monitor` dies with the session that armed it, and the next session inherits its PRs silently. So **arming
watches is part of session startup**: `session-register` (startup step 4) re-arms one multi-PR watch over every
open PR you take over, from the predecessor's `## Open PRs` list; `session-handoff` refreshes that list before
ending. Never assume a watch exists because the context doc says one was armed.

## Arm a watch

```
Monitor({
  command: "PR_WATCH_WORKTREE=<checkout> bash $BATON/skills/pr-watch/pr-watch.sh <org>/<repo> <pr1> <head1> <pr2> <head2> <pr3> <head3>",   // always via `bash …` (reference/arming.md)
  description: "<repo> #<pr1>/#<pr2>/#<pr3>: actionable events only, until merged",
  timeout_ms: 1800000   // the harness caps every Monitor at 30 min (a larger value is silently capped)
})
```

`<checkout>` is a local checkout of the repo the session pushes from (any worktree of it): the session's own
pushes are then tracked silently instead of waking it as `HEAD MOVED`; leave it out when pushes come from another
machine. All PRs of one repo go in one process; a second repo needs a second Monitor. **Re-arm on expiry = the
identical call** (same command, original heads): the state dir makes it silent, so never look the heads up again.
Arguments, state dir, identity (`PR_WATCH_SELF`; a failed lookup exits 2, never a placeholder identity) and the
no-bot mode (`github.review_bot` empty → gate on CI + human review only): `reference/arming.md`.

## What it emits

One stdout line per actionable event — bot verdict on the current head, human comment/approval, checks settling
red, a real head move, conflicts, merge/close, or a query error — each with what to do about it. Bookkeeping
(its own `update-branch` sync, this session's own push, a bot's stale review) goes to stderr, never a wake-up; a
human's stale review is still emitted, marked `(on older head <sha>)`. **A failed lookup is UNKNOWN, never red or
green**: it prints `PR N LOOKUP FAILED: <what> — <error>` after 3 attempts, and a repeated alarm backs off
(1h, 3h, 5h …). The per-line table: `reference/events.md`; the backoff and mute knobs: `reference/cost-controls.md`.

## Auto-sync with the base branch

While a PR waits for review the watcher keeps it merged with its base: when behind and **no human `APPROVED`
review exists**, it runs `update-branch` itself (once per head, `PR_WATCH_SYNC=0` turns it off) and logs `SYNCED`
to stderr. **Standing rule: before any further push from the worktree**, `git fetch origin <branch>` and, when the
remote is ahead, rebase onto it first (`sign-queue --rebase` where commits are signed). Detail:
`reference/auto-sync.md`.

## Park when the gates are not yours

On each expiry, look back: if the previous **two consecutive windows** delivered zero actionable events, do
**not** re-arm. A PR waiting on a gate only the user's machine, a human or a third party can pass is not something
a live session makes happen faster, and an idle watcher still pays every expiry (`reference/cost-controls.md`
§ Why park). Three park triggers, any one is enough:

1. **The user signs off** (says they are leaving, or it is outside their working hours and nothing is mid-flight) —
   stop the watcher and end the session **now**, do not wait for two idle windows.
2. **Two consecutive idle windows** (above).
3. **The only pending event is a human's approval or a gate only the user's machine can pass** (`update-branch` 403, catalog bump,
   feature flag, a third party's reply) — never re-arm for that; the successor re-arms when the user reports it.

Stopping is `TaskStop` on the Monitor, then run `session-handoff` (step 7 stops the watch, step 9 records the
heads the successor re-arms from) and end the session.

## Triage each event on the `triage` agent — `pr-event-brief`

Don't read the PR yourself at full prefix when a line lands. Invoke the forked skill
`pr-event-brief <owner/repo> <n> "<event line>"` (Skill tool; it runs on the `triage` agent and blocks until it
returns). You get a ≤10-line brief ending in one `ACTION:` (`REPLY+RESOLVE` / `FIX+PUSH` / `RE-REQUEST-BOT` /
`UPDATE-BRANCH` / `MERGE` / `WAIT(<who>)` / `INVESTIGATE-CHECK` / `REDRAW`). **You perform the action** — replies,
resolves, re-requests, merges post to GitHub and stay on the main model. On a real `HEAD MOVED`, re-request the bot
and run `python3 $BATON/skills/pr-open/diagram-plan.py --pr <o/r> <n> --check` yourself — `OK` / `NO MARKER` →
nothing; `DRIFT` (incl. a malformed marker) → fork `pr-event-brief` with the HEAD MOVED line only and act on its
`REDRAW` per `docs/diagrams.md`. Also run `python3 $BATON/skills/pr-open/codeowners.py check <o/r> --pr <n> [--ignore <github.review_bot>]`:
exit 0 → nothing; exit 3 `OWNERS CHANGED +…` → request the `+` owners; exit 2 is a failed read, never "OK"
(`$BATON/skills/pr-open/reference/codeowners.md`). Skip the fork for `MERGED`/`CLOSED` (close out per
`reference/events.md`), `LOOKUP FAILED` and `ERROR … startup:` — a failed read is not a PR event. A brief slot
marked `unverified` means fetch it yourself.

Whether to merge past a settled red check (a known-flaky test, an annotation you already judged harmless) or wait
for the rerun is the user's call, not a default — ask it per `docs/carousel.md`.

## Rules this encodes (verified on a strict-ruleset repo with a review bot)

Unresolved threads block merge (reply and resolve every one — an approver's own threads too, once they approved
after our reply; a thread waiting on anyone else stays theirs); a BEHIND PR needs `update-branch` then a forced bot
re-review, done yourself except on a workflow-file PR (403 → rebase + `sign-queue --rebase`); re-request the bot with
DELETE+POST; test the bot verdict alone, never mixed with a human's `APPROVED`; one watcher per PR. Full rules: `reference/rules.md`.

## Merge the PR — `pr-merge.sh` (do not hand-roll the gates)

```
Monitor({
  command: "bash $BATON/skills/pr-watch/pr-merge.sh <org>/<repo> <n>",
  description: "#<n> merge sequence: bot verdict → update-branch → forced review → squash-merge when CLEAN",
  timeout_ms: 1800000   // an expiry kills the script: rerun it, every gate is re-read from the PR
})
```

It waits for the bot's Assessment on the current head, syncs a BEHIND branch and forces a re-review, then
squash-merges on `CLEAN` + `reviewDecision APPROVED` + zero unresolved threads, confirmed by re-reading `merged_at`.
Exit 0 = merged; 1 = a gate or the merge failed (one-line reason — act, then rerun); 3 = gave up (CLEAN + APPROVED
never held within the deadline). A body ending in a `BREAKING CHANGE:` footer needs the hand merge with the footer
lines only. Phases and the footer rule: `reference/rules.md` § pr-merge.sh phases.

## Staleness rules

Paginate every listing; verdict = the review object on the final head sha via `bot-verdict.sh`; test the bot
variable alone; a head move means rerun the merge script; `bash` in front of the script path. Why:
`reference/staleness.md`.
