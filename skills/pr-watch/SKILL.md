---
name: pr-watch
description: "Low-noise PR watch: one Monitor per repo/session surfaces only actionable events, triages via `pr-event-brief`, keeps branches synced, merges via `pr-merge.sh` once gates hold. Parks on sign-off, idle windows or a human-only gate. Use when your session owns an open PR."
metadata:
  version: "27"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
---

# pr-watch — stay on top of your PRs without the noise

**One `Monitor` per repo per session**, covering every open PR that session owns in that repo (owner
decision, 2026-09-22 — one process round-robins the PRs per repo, `sleep 120` per cycle). Polling is
free; only a real event or the Monitor's own 30-min expiry costs a wake-up at full prefix (~$0.30 each).

## Watches are session-scoped — re-arm on every session start

A `Monitor` dies with the session that armed it, so **arming watches is part of session startup**:
`session-register` (startup step 4) re-arms one multi-PR watch over every PR you take over, from the
predecessor's `## Open PRs` list; `session-handoff` refreshes that list before ending. Never assume a
watch exists because the context doc says one was armed.

## Arm a watch

```
Monitor({
  command: "PR_WATCH_WORKTREE=<checkout> bash $BATON/skills/pr-watch/pr-watch.sh <org>/<repo> <pr1> <head1> <pr2> <head2> <pr3> <head3>",   // always via `bash …`: the file's execute bit is not reliable on this mount (exit 126)
  description: "<repo> #<pr1>/#<pr2>/#<pr3>: actionable events only, until merged",
  timeout_ms: 1800000   // the harness caps every Monitor at 30 min (a larger value is silently capped)
})
```

`<checkout>` is a local checkout of the repo the session pushes from — any of its worktrees does, they
share one object store. With it set, the session's own pushes are tracked silently instead of waking it
as `HEAD MOVED`; leave it out when the pushes come from another machine. Arguments: `<owner/repo>
<pr_number> <head_sha_prefix> [<pr_number> <head_sha_prefix> …]` — all PRs of one repo in one process (a
second repo needs a second Monitor); the single-PR form still works unchanged. Per-PR state lives in
`${TMPDIR:-/tmp}/pr-watch-<owner-repo>-<pr>/`; a merged/closed PR drops out of the round-robin and the
process exits when the last one is gone. The script applies `github.sandbox_token_prefix` itself.

**Re-arm on expiry = the identical call**, same heads: the state dir makes it silent (nothing already
reported is replayed), so an expiry costs its one wake-up and nothing more — never look the heads up
again for it. Set `PR_WATCH_SELF=<your GitHub login>` if it is not `$WORKSPACE_GITHUB_LOGIN`; events by
that login are dropped as your own. Neither set and `gh api user` fails (or names nobody): startup prints
`ERROR … startup:` and exits 2 — it never falls back to a placeholder identity, since that would turn
every one of this session's own replies into a new event.

**No bot configured (`github.review_bot` empty — the default on most machines).** The watcher prints
which mode it is in on line 1 (stderr) and never polls for a bot Assessment; `pr-merge.sh` does the
same — the merge gate becomes `mergeStateStatus CLEAN` + `reviewDecision APPROVED` + zero unresolved
threads, same as the bot-mode phase 3 loop.

## What it emits (and what it deliberately does not)

One stdout line per actionable event — bot verdict, human comment/approval, checks settling red, a head
move, conflicts, merge/close, or a query error — each with what to do about it. The watcher's own
bookkeeping (its `update-branch` sync, a head move this session's own push produced, a stale review from
the configured review bot or a `github.bots` login) goes to stderr only, never a wake-up; your own
comments/reviews, the bot's in-thread replies and repeated non-green states are filtered the same way. A
human's stale review is still emitted, marked `(on older head <sha>)` — humans do not auto-re-review
after a push. **A failed lookup is UNKNOWN, never red or green**: 3 retries, then one `LOOKUP FAILED`
line, never a silent fallback. **A repeated alarm backs off additively** (1h, 3h, 5h … +2h, capped by
`PR_WATCH_BACKOFF_MAX`), and `PR_WATCH_KNOWN_RED` mutes a matched cause until its next green settle. Full
per-line table, retry/backoff timing and the mute rule: `reference/events.md`, `reference/cost-controls.md`.

## Auto-sync with the base branch (since 2026-09-21)

While a PR waits for review it cannot merge anyway, so the watcher keeps the branch merged with its
base: every poll it reads `behind_by`, and when behind with **no human `APPROVED` review** (a
bot/automerge-bot approval doesn't count), runs `update-branch` itself — cooled down by
`PR_WATCH_SYNC_COOLDOWN`, off via `PR_WATCH_SYNC=0` — and logs `SYNCED` to stderr only. **Standing rule:
before any further push from the worktree**, `git fetch origin <branch>` and rebase onto it if ahead
(`sign-queue --rebase` where signed) — the watcher may have auto-synced the branch behind your back.
Approval-detection nuance and failure cases: `reference/auto-sync.md`.

## Cost controls (2026-09-22)

What costs is every line emitted and every Monitor expiry (~$0.30 each at a ~120k prefix). Knob table
(`PR_WATCH_SELF`/`SYNC`/`SYNC_COOLDOWN`/`KNOWN_RED`/`REPLAY`), the once-per-head `CHECK NOT GREEN` rule
and the silent-re-arm behaviour: `reference/cost-controls.md`.

## Park when the gates are not yours (2026-09-22)

Arm **one** Monitor per repo per session for all its PRs there, `timeout_ms: 1800000` (the harness
maximum). On each expiry, look back: two consecutive idle windows (only expiries, muted CI lines) →
do **not** re-arm — run `session-handoff` and end the session. The successor re-arms from `## Open PRs`
(`session-register` step 4) once the user reports the gate moved.

Cost: one Monitor per PR ≈ $1.80/h idle for 3 PRs vs. one multi-PR Monitor ≈ $0.60/h; parked = $0 — a PR
waiting on a human-only gate does not go faster for a live session watching it. Three park triggers, any
one is enough (an unparked session once ran three 30-min watchers all night, ~$85, 2026-09-21/22):
**the user signs off** (leaving, or outside working hours with nothing mid-flight) — stop and end **now**,
don't wait for two idle windows; **two consecutive idle windows** (above); **the only pending event is a
human-only gate** (approval, `update-branch` 403, catalog bump, feature flag, a third party's reply) —
never re-arm for that.

Stopping is `TaskStop` on the Monitor; nothing on GitHub changes. `session-handoff` step 7 is where the
stop belongs; step 9 records the heads the successor re-arms from.

## Triage each event on Sonnet — `pr-event-brief`

Don't read the PR yourself at full prefix when a line lands. Invoke the forked skill `pr-event-brief
<owner/repo> <n> "<event line>"` (Skill tool; runs on the `triage` agent — Sonnet, low effort, no
CLAUDE.md — blocks until it returns): a ≤10-line brief ending in one `ACTION:` (`REPLY+RESOLVE` /
`FIX+PUSH` / `RE-REQUEST-BOT` / `UPDATE-BRANCH` / `MERGE` / `WAIT(<who>)` / `INVESTIGATE-CHECK` /
`REDRAW`). **You perform the action** on the main model — replies, resolves, re-requests, merges post to
GitHub. On a real `HEAD MOVED`, re-request the bot and run `python3
$BATON/skills/pr-open/diagram-plan.py --pr <o/r> <n> --check` yourself: `OK`/`NO MARKER` → nothing;
`DRIFT` (incl. a malformed marker) → fork `pr-event-brief` with the HEAD MOVED line, act on its `REDRAW`
per `docs/diagrams.md` (the watcher's own sync emits no head move and gets no check). Skip the fork for
`MERGED`/`CLOSED` (close out per `reference/events.md`) and `LOOKUP FAILED`/`ERROR … startup:` (a failed
read is not a PR event). A brief slot marked `unverified` means fetch it yourself.

Merging past a settled red check (a known-flaky test, an annotation you already judged harmless) vs.
waiting for the rerun is the user's call, not a default — ask per `docs/carousel.md`.

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

Phases: (1) wait for the review bot's **Assessment on the current head**, read from the review *object*
(a green "PR Review" check is not a review); (2) if BEHIND, `update-branch`, then force a full bot
review on the merge head with a DELETE + POST re-request (a plain `POST` alone is a no-op on a
merge-commit head; the draft-toggle fallback, § Rules this encodes, is visible to every other reviewer
and cancels a `ready_for_review`-triggered run, so `pr-merge.sh` uses neither), wait again; (3)
squash-merge when `CLEAN` + `reviewDecision APPROVED` + zero unresolved threads, **confirmed by
re-reading `merged_at`** — a `gh pr merge` success with `merged_at` unset is not read as merged. Exit 0 =
confirmed merged; exit 1 = a gate failed, the merge call failed, or `merged_at` came back unset (reason
given: head moved, open threads, not green, approval missing); exit 3 = gave up (CLEAN + APPROVED never
held within the deadline).

## Staleness rules — why the scripts look the way they do

Paginate every listing (30/page hides a newer bot verdict); verdict = the review object on the final
head sha via `bot-verdict.sh`, never a check run or an older head's review; test the bot variable alone,
never mixed with a human `APPROVED`; a head move means rerun the merge script; `bash` in front of the
script path (the execute bit comes and goes). Full rationale: `reference/staleness.md`.
