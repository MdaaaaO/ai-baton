---
name: pr-watch
description: "Low-noise PR watch: one Monitor per repo per session surfaces only actionable events (review-bot verdict, others' reviews/comments, a settled red check, head moves, merge/close), keeps waiting branches updated with base, merges via `pr-merge.sh` once gates hold. Park rule: sign-off, idle windows, human gate. For every PR your session owns."
metadata:
  version: "19"
  updated: "2026-09-30"
  reviewed: "2026-09-27"
---

# pr-watch — stay on top of your PRs without the noise

**One `Monitor` per repo per session**, covering every open PR that session owns in that repo (owner
decision, 2026-09-22 — the script takes one repo; one process round-robins the PRs, one `sleep 120` per
cycle). The script polls GitHub every 120 s and prints
one line per actionable event; the harness turns each line into a notification, so the polling is free
and only real events — plus the Monitor's own expiry, every 30 min — cost a wake-up at full prefix (~$0.30 each).

## Watches are session-scoped — re-arm on every session start

A `Monitor` dies with the session that armed it. Ending a session (cost, compaction, crash) leaves
its PRs unwatched, and the next session inherits them silently. So **arming watches is part of
session startup**: the `session-register` skill (startup step 4) re-arms one multi-PR watch over every
open PR you take over, from the predecessor's `## Open PRs` list; the `session-handoff` skill refreshes that
list before ending. Never assume a watch exists because the context doc says one was armed.

## Arm a watch

```
Monitor({
  command: "bash $BATON/skills/pr-watch/pr-watch.sh <org>/<repo> <pr1> <head1> <pr2> <head2> <pr3> <head3>",   // always via `bash …`: the file's execute bit is not reliable on this mount (exit 126). The script is POSIX-safe since 2026-09-14 (a bash-only `${cur:0:9}` in the HEAD MOVED branch crashed a `sh`-run watcher with "Bad substitution" on the first head move)
  description: "<repo> #<pr1>/#<pr2>/#<pr3>: actionable events only, until merged",
  timeout_ms: 1800000   // the harness caps every Monitor at 30 min (a larger value is silently capped)
})
```

**Re-arm on expiry = the identical call**, same command, original heads: the state dir makes it silent
(nothing already reported is replayed, and a head the watcher already followed is kept, not reported
again), so an expiry costs its one wake-up and nothing more. Never look the heads up again for it.

Arguments: `<owner/repo> <pr_number> <head_sha_prefix> [<pr_number> <head_sha_prefix> …]` — all PRs of
one repo in one process. The single-PR form (`… <owner>/<repo> <pr> <sha>`) still works unchanged;
a second repo needs a second Monitor. Per-PR state lives in `${TMPDIR:-/tmp}/pr-watch-<owner-repo>-<pr>/`
(one file per variable). A merged/closed PR prints its line and drops out of the round-robin; the
process exits when the last one is gone. The script applies `github.sandbox_token_prefix` itself (a
placeholder token in a sandbox whose proxy injects credentials, nothing where `gh` is logged in). Set `PR_WATCH_SELF=<your GitHub login>` if it is not `$WORKSPACE_GITHUB_LOGIN` (the user's login);
events by that login are dropped as your own. Neither set and `gh api user` fails (or names nobody):
startup prints an `ERROR … startup:` line and exits 2 — it never falls back to a placeholder identity,
since that would stop filtering this session's own replies and turn every one of them into a new event.

**No bot configured (`github.review_bot` empty — the default on most machines).** The watcher prints
which mode it is in on the first line (`pr-watch: bot mode …` / `pr-watch: no-bot mode … — gating on CI
+ human review only`, stderr) and never polls for a bot Assessment: no `BOT REVIEW` line, no wait, no
wasted paginated `reviews` fetch every cycle. `pr-merge.sh` does the same on its own first line — the
merge gate becomes `mergeStateStatus CLEAN` + `reviewDecision APPROVED` + zero unresolved threads, same
as the bot-mode phase 3 loop.

## What it emits (and what it deliberately does not)

One line per actionable event — bot verdict, human comment/approval, checks settling red, head moves
(pushed, synced, or stale), conflicts, merge/close, or a query error — each with what to do about it;
filtered out on purpose: your own comments/reviews, the bot's in-thread replies, repeated non-green
states. The full per-line table: `reference/events.md`.

## Auto-sync with the base branch (since 2026-09-21)

While a PR waits for review it cannot merge anyway, so the watcher keeps the branch merged with its base:
every poll it reads `compare/<base>...<head>.behind_by`, and when behind and **no human `APPROVED`
review exists** (a bot/automerge-bot approval does not count), it runs `update-branch` itself and prints
`SYNCED` — one attempt per head, cooled down by `PR_WATCH_SYNC_COOLDOWN`, off via `PR_WATCH_SYNC=0`. The
approval-detection nuance, the failure cases and the consequences (rebase needs `sign-queue --rebase`,
the bot must re-review): `reference/auto-sync.md`.

## Cost controls (2026-09-22)

The shell polling is free; what costs is every line emitted and every Monitor expiry (~$0.30 each at a
~120k prefix). `PR_WATCH_SELF` / `PR_WATCH_SYNC` / `PR_WATCH_SYNC_COOLDOWN` / `PR_WATCH_KNOWN_RED` /
`PR_WATCH_REPLAY` — the full table, the once-per-head `CHECK NOT GREEN` rule and the silent-re-arm
behaviour that motivates it: `reference/cost-controls.md`.

## Park when the gates are not yours (2026-09-22)

Arm **one** Monitor per repo per session for all its PRs there, `timeout_ms: 1800000` (the harness maximum). On each expiry,
look back: if the previous **two consecutive windows** delivered zero actionable events (only expiries,
muted CI lines), do **not** re-arm — run the `session-handoff` skill and end the session. The successor
re-arms from the `## Open PRs` list (`session-register` step 4) when the user reports the gate moved.

Arithmetic: 3 PRs × one Monitor each ≈ 6 expiry wake-ups/h ≈ $1.80/h idle; one multi-PR Monitor ≈ 2/h ≈ $0.60/h;
parked = $0. A PR waiting on an `update-branch` only the user's machine can run, a human review or a third party is not
something a live session makes happen faster.

Three park triggers, any one is enough (one idle session with three
30-min watchers ran 30–40 turns/h all night, ~$85, 2026-09-21/22):

1. **The user signs off** (says they are leaving, or it is outside their working hours and nothing is mid-flight) —
   stop the watcher and end the session **now**, do not wait for two idle windows.
2. **Two consecutive idle windows** (above).
3. **The only pending event is a human's approval or a gate only the user's machine can pass** (`update-branch` 403, catalog bump,
   feature flag, a third party's reply) — never re-arm for that; the successor re-arms when the user reports it.

Stopping is `TaskStop` on the Monitor; nothing on GitHub changes. The `session-handoff` skill's step 7 is
where the stop belongs, and step 9 records the heads the successor re-arms from.

## Triage each event on Sonnet — `pr-event-brief`

Don't read the PR yourself at full prefix when a line lands. Invoke the forked skill
`pr-event-brief <owner/repo> <n> "<event line>"` (Skill tool; it runs on the `triage` agent — Sonnet,
low effort, no CLAUDE.md — and blocks until it returns). You get a ≤10-line brief ending in one
`ACTION:` (`REPLY+RESOLVE` / `FIX+PUSH` / `RE-REQUEST-BOT` / `UPDATE-BRANCH` / `MERGE` / `WAIT(<who>)` /
`INVESTIGATE-CHECK`). **You perform the action** — replies, resolves, re-requests, merges post to
GitHub and stay on the main model. Skip the fork for `HEAD MOVED` (just re-request the bot; the
watcher's own sync emits none — it re-requests the bot itself),
`MERGED`/`CLOSED` (close out per `reference/events.md`), and `ERROR …` (never fork
`pr-event-brief` for it — it is not a PR event, there is nothing on the PR to triage). A brief slot
marked `unverified` means fetch it yourself.

## Rules this encodes (verified on a strict-ruleset repo with a review bot)

Unresolved threads block merge (reply and resolve every one); a BEHIND PR needs `update-branch` then a
forced bot re-review, done yourself except on a workflow-file PR (403 → rebase + `sign-queue --rebase`);
re-request the bot with DELETE+POST; test the bot verdict on its own, never mixed with a human's
`APPROVED`; one watcher per PR across sessions. Full rules and the exact commands: `reference/rules.md`.

## Merge the PR — `pr-merge.sh` (do not hand-roll the gates)

```
Monitor({
  command: "bash $BATON/skills/pr-watch/pr-merge.sh <org>/<repo> <n>",
  description: "#<n> merge sequence: bot verdict → update-branch → forced review → squash-merge when CLEAN",
  timeout_ms: 1800000   // an expiry kills the script: rerun it, every gate is re-read from the PR
})
```

Phases: (1) wait for the review bot's **Assessment on the current head**, read from the review
*object* (a green "PR Review" check is not a review); (2) if BEHIND, `update-branch`, force a full
bot review on the merge head with a DELETE + POST re-request of the bot as a requested reviewer — a
single, plain re-request (`POST` alone) is a no-op on a merge-commit head, and the draft-toggle
fallback named above (§ Rules this encodes: `gh pr ready --undo && gh pr ready`) is visible to every
other reviewer and cancels a `ready_for_review`-triggered run, so `pr-merge.sh` uses neither of
those and does the DELETE+POST itself), wait again; (3) squash-merge when `CLEAN` +
`reviewDecision APPROVED` + zero unresolved threads,
**confirmed by re-reading the PR's `merged_at`** — a `gh pr merge` call that reports success but
leaves `merged_at` unset is not read as merged. Exit 0 = confirmed merged; exit 1 = a gate failed,
the merge call failed, or `merged_at` came back unset (one-line reason: head moved, open threads,
not green, approval missing — read it and rerun after acting); exit 3 = gave up (CLEAN + APPROVED
never held within the deadline).

## Staleness rules — why the scripts look the way they do

Paginate every listing (30/page hides a newer bot verdict); verdict = the review object on the final
head sha via `bot-verdict.sh`, never a check run or an older head's review; test the bot variable alone,
never mixed with a human `APPROVED`; a head move means rerun the merge script; `bash` in front of the
script path (the execute bit comes and goes). Full rationale: `reference/staleness.md`.
