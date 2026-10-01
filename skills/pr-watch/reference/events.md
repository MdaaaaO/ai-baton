# What the watcher emits (and what it deliberately does not)

Every line below is stdout: the harness turns it into a wake-up. Everything else — bookkeeping the
session has nothing to do about — goes to stderr instead (the Monitor output file), never a wake-up:
the watcher's own `update-branch` sync (`SYNCED` — a log line, not an event; `reference/auto-sync.md`
and the SKILL.md standing rule are what tell a session to check for it before its own next push), its
own re-request of the bot after that sync, a head move this session's own push produced (named by
committer login + a local git object in `PR_WATCH_WORKTREE`, when set), and a stale review (`commit_id`
not the PR's current head) from the configured review bot or a login in `github.bots` — a bot re-reviews
the new head on its own once asked.

| Line | Meaning | Do |
|---|---|---|
| `PR N BOT REVIEW on <head>: green\|yellow\|red` | the review bot (`github.review_bot`) posted a full review for this head — the color from its `### Assessment:` line, via the shared `bot-verdict.sh` helper | read threads; fix or reply; resolve; re-request the bot after a push |
| `PR N NEW: review comment <id> by <login> (top)` | a human (or the bot's first-pass thread) commented | address it |
| `PR N NEW: review <STATE> by <login>` | human review verdict (`APPROVED` / `CHANGES_REQUESTED` / `COMMENTED`) **on the current head**; `… (on older head <sha>)` marks one given on an earlier head — a human does not automatically re-review after a push, so it is still live information this cycle (an `APPROVED` there still counts toward the merge unless the branch rule dismisses stale reviews; a `CHANGES_REQUESTED` there still blocks it). Only a stale verdict from the configured review bot or a `github.bots` login is dropped, with a stderr note | update-branch if BEHIND, confirm bot verdict on the final head, merge or address the feedback |
| `PR N CHECK NOT GREEN: …` | the check suite settled and some checks failed/cancelled — **one line per head**, listing all of them | investigate (a check cancelled right after a draft toggle is normal — the rerun follows) |
| `PR N HEAD MOVED to <sha>` | someone pushed or clicked Update branch — never the watcher's own sync, and never this session's own push when `PR_WATCH_WORKTREE` names a local checkout (reference/auto-sync.md) | re-request the bot · `diagram-plan.py --check`; DRIFT → fork pr-event-brief → redraw |
| `PR N RE-REQUEST of <bot> on <sha> failed after the auto-sync: …` | the remove-and-re-add of the review bot after the watcher's own sync was refused (403, 422, rate limit) | re-request by hand: DELETE then POST `requested_reviewers` (reference/rules.md) |
| `PR N BEHIND <base> … but APPROVED — not auto-syncing` | the branch is stale but a human approval exists, and a push would dismiss it where `dismiss_stale_reviews_on_push` is on | merge now if the ruleset allows a stale branch; otherwise update-branch yourself and ask for re-approval |
| `PR N BEHIND <base> … update-branch refused (403 …)` | the token `gh` runs with lacks the `workflow` scope (a proxy-injected token usually does) and the PR touches `.github/workflows/**` | rebase the worktree + `sign-queue --rebase`, or hand the user `cd <repo> && gh pr update-branch N` for their own machine |
| `PR N BEHIND <base> … update-branch 422 (merge conflict or head moved): …` | the update-branch call was refused — re-read before this line fires: merged/closed by then prints only `MERGED`/`CLOSED` below, nothing BEHIND | a real conflict: rebase the worktree + `sign-queue --rebase` |
| `PR N CONFLICTS with <base>` | `mergeable_state=dirty` | rebase the worktree, resolve, `sign-queue --rebase` |
| `PR N MERGED` / `CLOSED` | done | verify the deploy by image tag; close the ticket; the Monitor exits |
| `ERROR <repo>#<pr> <gh stderr first line>` (or `ERROR <repo> startup: …` before any PR is polled) | a `gh`/`graphql` call failed (auth, rate limit, the PR/repo is gone), or the bot-login lookup failed at startup — no `PR N` prefix, since it is not a state change | a one-off is transient, ignore it; the same error every cycle means the PR or token is gone — stop the watch or fix auth. **Never fork `pr-event-brief` for this line** — it is not a PR event, there is nothing on the PR to triage |

Filtered out on purpose: your own comments/reviews, the review bot's in-thread replies ("Perfect, thanks…"),
repeated non-green states, and a stale verdict (`commit_id` not the current head) from the configured
review bot or a `github.bots` login.
