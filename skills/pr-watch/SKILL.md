---
name: pr-watch
description: Low-noise watch on the PRs you authored: ONE multi-PR Monitor per repo per session, emitting only actionable events (review-bot verdict, others' reviews/comments, a settled red check, head moves, merge/close), keeping waiting branches updated with their base, and merging via `pr-merge.sh` once the gates hold. Park rule: sign-off, idle windows, human gate. For every open PR your session owns.
metadata:
  version: "13"
  updated: "2026-09-27"
  reviewed: "2026-09-26"
---

# pr-watch — stay on top of your PRs without the noise

**One `Monitor` per repo per session**, covering every open PR that session owns in that repo (owner
decision, 2026-09-22 — the script takes one repo; one process round-robins the PRs, one `sleep 120` per
cycle). The script polls GitHub every 120 s and prints
one line per actionable event; the harness turns each line into a notification, so the polling is free
and only real events — plus the Monitor's own expiry — cost a wake-up at full prefix (~$0.30 each).

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
  persistent: true, timeout_ms: 3600000   // the maximum — one expiry per hour, not two per hour per PR
})
```

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

| Line | Meaning | Do |
|---|---|---|
| `PR N BOT REVIEW on <head>: green\|yellow\|red` | the review bot (`github.review_bot`) posted a full review for this head — the color from its `### Assessment:` line, via the shared `bot-verdict.sh` helper (below) | read threads; fix or reply; resolve; re-request the bot after a push |
| `PR N NEW: review comment <id> by <login> (top)` | a human (or the bot's first-pass thread) commented | address it |
| `PR N NEW: review APPROVED by <login>` | human approval | update-branch if BEHIND, confirm bot verdict on the final head, merge |
| `PR N CHECK NOT GREEN: …` | the check suite settled and some checks failed/cancelled — **one line per head**, listing all of them | investigate (a check cancelled right after a draft toggle is normal — the rerun follows) |
| `PR N HEAD MOVED to <sha>` | someone pushed or clicked Update branch (or the watcher synced — see below) | re-request the bot; the script keeps tracking the new head itself |
| `PR N SYNCED with <base> (was k behind)` | the watcher ran `update-branch` itself (owner decision, 2026-09-21: "keep the branch updated while we wait, since we can't merge anyway") | nothing to do; `HEAD MOVED` follows; note in the session file that further worktree pushes need `sign-queue --rebase` |
| `PR N BEHIND <base> … but APPROVED — not auto-syncing` | the branch is stale but a human approval exists, and a push would dismiss it where `dismiss_stale_reviews_on_push` is on | merge now if the ruleset allows a stale branch; otherwise update-branch yourself and ask for re-approval |
| `PR N BEHIND <base> … update-branch refused (403 …)` | the token `gh` runs with lacks the `workflow` scope (a proxy-injected token usually does) and the PR touches `.github/workflows/**` | rebase the worktree + `sign-queue --rebase`, or hand the user `cd <repo> && gh pr update-branch N` for their own machine |
| `PR N CONFLICTS with <base>` | `mergeable_state=dirty` | rebase the worktree, resolve, `sign-queue --rebase` |
| `PR N MERGED` / `CLOSED` | done | verify the deploy by image tag; close the ticket; the Monitor exits |
| `ERROR <repo>#<pr> <gh stderr first line>` (or `ERROR <repo> startup: …` before any PR is polled) | a `gh`/`graphql` call failed (auth, rate limit, the PR/repo is gone), or the bot-login lookup failed at startup — no `PR N` prefix, since it is not a state change | a one-off is transient, ignore it; the same error every cycle means the PR or token is gone — stop the watch or fix auth. **Never fork `pr-event-brief` for this line** — it is not a PR event, there is nothing on the PR to triage |

Filtered out on purpose: your own comments/reviews, the review bot's in-thread replies ("Perfect, thanks…"),
and repeated non-green states.

## Auto-sync with the base branch (since 2026-09-21)

While a PR waits for review it cannot merge anyway, so a branch that drifts behind `main` only adds a
round trip later (a stale base can make a bot's regenerated tree show deletions main had already made). The watcher therefore keeps the branch merged with its base:

- Every poll it reads `compare/<base>...<head>.behind_by`; when > 0 and **no human `APPROVED` review
  exists** (approvals on any head — a push would dismiss them where the ruleset says so) it runs
  `PUT pulls/N/update-branch` with `expected_head_sha` (GitHub-signed merge commit) and prints `SYNCED`.
  An approval by the configured review bot (`github.review_bot`) or by a login in the configured
  `github.bots` list (which covers the identity auto-merge.yml's own approval carries — a different
  login than the review bot's own account when it posts its Assessment; never hardcoded, gh-cli
  SKILL.md) does not count as that human approval: a PR approved only by one of
  those still gets synced, since nothing would dismiss a bot's own review and the alternative is a
  BEHIND, bot-approved PR that never reaches auto-merge. One attempt per head; `PR_WATCH_SYNC_COOLDOWN`
  (default 3600 s) stops a busy `main` from restarting the PR's CI every two minutes; the cooldown is
  seeded from the head commit when it is a GitHub `web-flow` merge commit, so a re-armed watcher
  (30-min Monitor cap) does not restart the clock at zero. `PR_WATCH_SYNC=0` turns it off (e.g. a PR
  someone is mid-review on, or a branch the user is about to force-push).
- A failure (403 workflow scope, 422 conflict) is reported once per head and not retried — act per the
  table above. `mergeable_state=dirty` is reported as `CONFLICTS` and never touched.
- Consequences you own: a merge commit lands on the branch, so any further push from the worktree needs
  `sign-queue --rebase`; the bot must review the new head (`HEAD MOVED` rule); CI re-runs on the PR.
  The sync never fires on an approved PR — on approval, `pr-merge.sh` / `update-branch` by hand is the path.

## Cost controls (2026-09-22)

The shell polling is free; what costs is every line emitted and every Monitor expiry (~$0.30 each at a
~120k prefix). Three knobs, on top of the one-process-per-session form above:

| Variable | Default | Effect |
|---|---|---|
| `PR_WATCH_SELF` | `$WORKSPACE_GITHUB_LOGIN` (else `gh api user`) | events by that login are dropped as your own |
| `PR_WATCH_SYNC` | `1` | `0` disables the auto `update-branch` (see above) |
| `PR_WATCH_SYNC_COOLDOWN` | `3600` | minimum seconds between two syncs of the same PR |
| `PR_WATCH_KNOWN_RED` | unset | extended regex; mutes a `CHECK NOT GREEN` whose failure annotations all match a cause you already know about |
| `PR_WATCH_REPLAY` | unset | `1` re-emits the current bot verdict / `CHECK NOT GREEN` on start; by default a re-arm on a head the state dir already knows is silent about what it already reported |

- **`CHECK NOT GREEN` fires at most once per head** (reset on `HEAD MOVED`) and only once the suite has
  settled — no check run still `queued`/`in_progress` — listing every failing check at that moment.
  Before 2026-09-22 it re-fired whenever the *set* of failing names changed, i.e. once per check that
  finished red — ~10 wake-ups for one cause. The rollup mixes check runs with legacy commit statuses (a
  required status context an external CI posts); both count toward pending and toward red — a status
  context carries no `status`/`conclusion` field of its own, only a `state`, which the watcher maps to
  the same tri-state a check run's `conclusion` uses.
- **`PR_WATCH_KNOWN_RED=<extended-regex>`** — when the red is a known, external cause, pass a regex over
  the *failure annotations*. Before emitting, the watcher fetches `check-runs/<id>/annotations` for every
  failing check run; it suppresses the line (stderr note only) **only if** each failing run has at least
  one `failure` annotation and *all* of them match. A failing check with zero annotations is unexplained →
  the line is emitted. So the regex must cover the generic wrappers too, e.g.
  `PR_WATCH_KNOWN_RED='<package-name>|Process completed with exit code'` mutes a known missing-export
  failure while a PR failing for another reason still reports. Drop the variable once the cause is fixed.
- **Silent re-arm.** The per-PR state dir (`${TMPDIR:-/tmp}/pr-watch-<owner>-<repo>-<pr>/`) survives the
  process, so a watcher re-armed on a head it already reported emits nothing until something changes —
  before 2026-09-22 every re-arm replayed the `BOT REVIEW` line for an unchanged head (one wasted wake-up per
  PR per re-arm; a third of one night's burn in the incident behind the park rule below). A different
  head, a missing state dir, or `PR_WATCH_REPLAY=1` restores the replay. The dir is shared by every session
  on the same machine: a successor taking over a PR inherits the silence and reads the current verdict from the
  predecessor's `## Open PRs` list instead.

## Park when the gates are not yours (2026-09-22)

Arm **one** Monitor per repo per session for all its PRs there, `timeout_ms: 3600000` (the maximum). On each expiry,
look back: if the previous **two consecutive windows** delivered zero actionable events (only expiries,
muted CI lines), do **not** re-arm — run the `session-handoff` skill and end the session. The successor
re-arms from the `## Open PRs` list (`session-register` step 4) when the user reports the gate moved.

Arithmetic: 3 PRs × 30-min Monitors ≈ 6 wake-ups/h ≈ $1.80/h idle; one 1 h multi-PR Monitor ≈ $0.30/h;
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
GitHub and stay on the main model. Skip the fork for `HEAD MOVED` (just re-request the bot) and
`MERGED`/`CLOSED` (close out per the table). A brief slot marked `unverified` means fetch it yourself.

## Rules this encodes (verified on a strict-ruleset repo with a review bot)

- The repo blocks merge on any unresolved thread, even a non-blocking LOW one — reply and resolve every thread.
  Every reply and comment on your own PR ends with `python3 $BATON/context-db/bin/kit_profile.py footer` (the session
  that wrote it); reviews on someone else's PR (`pr-review`) carry no footer.
- Strict required checks: a PR that is BEHIND main is refused; `PUT pulls/N/update-branch` first (GitHub-signed merge commit), then the bot must review the new head. **Do this yourself** — it works with the token `gh` runs with for every PR that does not touch `.github/workflows/**`. **Exception:** a PR carrying a workflow-file change gets 403 where that token lacks the `workflow` scope (a proxy-injected token usually does). Then rebase the worktree onto `origin/main` yourself and enqueue a `sign-queue` job with `--rebase` so the user's routine `make sign` pushes it; only if that is impractical hand them the one command for their own machine, `cd <abs repo> && gh pr update-branch <n>`. The merge itself never needs the user's machine: once `mergeable_state` is `clean`, `gh pr merge <n> --repo <o>/<r> --squash` goes through with the same token (`pr-merge.sh` runs the same command — no `--auto`).
- Re-request the bot with `DELETE` + `POST pulls/N/requested_reviewers` (the env config's `github.review_bot`). A run that finishes in ~20 s without posting = re-request again; `gh pr ready --undo && gh pr ready` is the fallback.
- Before an automated merge, test the bot verdict **on its own** — `bot-verdict.sh` (below) prints a plain `green`/`yellow`/`red`/`none`, so `case "$v" in green) …` never risks matching a combined string against the human `reviewDecision` `APPROVED`, the bug the old ad hoc regex had to guard against.
- Do not watch PRs another session owns — one watcher per PR *across sessions*. Within your own session
  that is still one **process** for several PRs (the multi-PR form), not one Monitor each.

Source of truth for the scripts: this directory — whichever session improves them copies the change back here (no master session since 2026-09-12).

## Merge the PR — `pr-merge.sh` (do not hand-roll the gates)

```
Monitor({
  command: "bash $BATON/skills/pr-watch/pr-merge.sh <org>/<repo> <n>",
  description: "#<n> merge sequence: bot verdict → update-branch → forced review → squash-merge when CLEAN",
  persistent: true, timeout_ms: 3600000
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

- **Paginate every listing.** `pulls/<n>/reviews`, `pulls/<n>/comments`, `issues/<n>/comments` return
  30 items per page. A PR with ten bot rounds has 40+ reviews, so a bare call never sees the newest
  verdict and a waiter sits "waiting for the bot" forever while the bot has already approved. Both scripts use `gh api --paginate "...?per_page=100"`; keep it that way in any new
  query, including one-off checks in the terminal.
- **Verdict = review object on the final head sha**, never a check run, never a review on an older
  head (`dismiss_stale_reviews` is off, so old approvals linger and look current). `bot-verdict.sh
  <owner/repo> <pr> <head>` is the one place this lookup lives — it replaced five hand-rolled versions
  of the same regex (fetch-context.sh, pr-scan.sh, pr-watch.sh, pr-merge.sh, trivial-check.py) that had
  started to drift; it prints `green`/`yellow`/`red`/`none` (exit 0), exits 2 when no bot is configured
  (never treat that as a failure — it is the no-bot branch above), and exits 1 with `gh`'s error on
  stderr when the API call itself fails (never read that as "none").
- **Test the bot variable alone**: `case "$verdict" in green)` — never match a string that also
  contains a human's `APPROVED` (that merges without a bot verdict).
- **Head moves reset everything**: rerun the merge script (the watcher already tracks the new head itself —
  nothing to re-arm there). GitHub's
  "Update branch" button (the user's clicks) is the usual cause; ask before clicking on a PR a session
  shepherds.
- **`bash` in front of the script path**: the execute bit on this mount comes and goes; exit 126 =
  you forgot it.
- The bot occasionally posts findings without an Assessment on a head; a second full run (draft
  toggle) produces one. A run that finishes in ~20 s posted nothing — force again.
