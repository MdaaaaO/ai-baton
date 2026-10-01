# What the watcher emits (and what it deliberately does not)

| Line | Meaning | Do |
|---|---|---|
| `PR N BOT REVIEW on <head>: green\|yellow\|red` | the review bot (`github.review_bot`) posted a full review for this head — the color from its `### Assessment:` line, via the shared `bot-verdict.sh` helper | read threads; fix or reply; resolve; re-request the bot after a push |
| `PR N NEW: review comment <id> by <login> (top)` | a human (or the bot's first-pass thread) commented | address it |
| `PR N NEW: review APPROVED by <login>` | human approval | update-branch if BEHIND, confirm bot verdict on the final head, merge |
| `PR N CHECK NOT GREEN: …` | the check suite settled and some checks failed/cancelled — **one line per head**, listing all of them | investigate (a check cancelled right after a draft toggle is normal — the rerun follows) |
| `PR N HEAD MOVED to <sha>` | someone pushed or clicked Update branch — never the watcher's own sync (reference/auto-sync.md) | re-request the bot · `diagram-plan.py --check`; DRIFT → fork pr-event-brief → redraw |
| `PR N SYNCED with <base> (was k behind)` | the watcher ran `update-branch` itself (owner decision, 2026-09-21: "keep the branch updated while we wait, since we can't merge anyway") | nothing to do: no `HEAD MOVED` follows and, in bot mode, the watcher re-requests the bot itself; note in the session file that further worktree pushes need `sign-queue --rebase` |
| `PR N RE-REQUEST of <bot> on <sha> failed after the auto-sync: …` | the remove-and-re-add of the review bot after the watcher's own sync was refused (403, 422, rate limit) | re-request by hand: DELETE then POST `requested_reviewers` (reference/rules.md) |
| `PR N BEHIND <base> … but APPROVED — not auto-syncing` | the branch is stale but a human approval exists, and a push would dismiss it where `dismiss_stale_reviews_on_push` is on | merge now if the ruleset allows a stale branch; otherwise update-branch yourself and ask for re-approval |
| `PR N BEHIND <base> … update-branch refused (403 …)` | the token `gh` runs with lacks the `workflow` scope (a proxy-injected token usually does) and the PR touches `.github/workflows/**` | rebase the worktree + `sign-queue --rebase`, or hand the user `cd <repo> && gh pr update-branch N` for their own machine |
| `PR N CONFLICTS with <base>` | `mergeable_state=dirty` | rebase the worktree, resolve, `sign-queue --rebase` |
| `PR N MERGED` / `CLOSED` | done | verify the deploy by image tag; close the ticket; the Monitor exits |
| `ERROR <repo>#<pr> <gh stderr first line>` (or `ERROR <repo> startup: …` before any PR is polled) | a `gh`/`graphql` call failed (auth, rate limit, the PR/repo is gone), or the bot-login lookup failed at startup — no `PR N` prefix, since it is not a state change | a one-off is transient, ignore it; the same error every cycle means the PR or token is gone — stop the watch or fix auth. **Never fork `pr-event-brief` for this line** — it is not a PR event, there is nothing on the PR to triage |

Filtered out on purpose: your own comments/reviews, the review bot's in-thread replies ("Perfect, thanks…"),
and repeated non-green states.
