# Auto-sync with the base branch (since 2026-09-21)

While a PR waits for review it cannot merge anyway, so a branch that drifts behind `main` only adds a
round trip later (a stale base can make a bot's regenerated tree show deletions main had already made). The watcher therefore keeps the branch merged with its base:

- Every poll it reads `compare/<base>...<head>.behind_by`; when > 0 and **no human `APPROVED` review
  exists** (approvals on any head — a push would dismiss them where the ruleset says so) it runs
  `PUT pulls/N/update-branch` with `expected_head_sha` (GitHub-signed merge commit) and logs `SYNCED`
  to stderr (the Monitor output file) — bookkeeping, not a decision, so it never wakes the session; the
  SKILL.md standing rule (`git fetch` + rebase before any further worktree push) is what a session reads
  instead of an event for this. An approval by the configured review bot (`github.review_bot`) or by a login in the configured
  `github.bots` list (which covers the identity auto-merge.yml's own approval carries — a different
  login than the review bot's own account when it posts its Assessment; never hardcoded, gh-cli
  SKILL.md) does not count as that human approval: a PR approved only by one of
  those still gets synced, since nothing would dismiss a bot's own review and the alternative is a
  BEHIND, bot-approved PR that never reaches auto-merge. One attempt per head; `PR_WATCH_SYNC_COOLDOWN`
  (default 3600 s) stops a busy `main` from restarting the PR's CI every two minutes; the cooldown is
  seeded from the head commit when it is a GitHub `web-flow` merge commit, so a re-armed watcher
  (every 30-min Monitor expiry) does not restart the clock at zero. `PR_WATCH_SYNC=0` turns it off (e.g. a PR
  someone is mid-review on, or a branch the user is about to force-push).
- A failure (403 workflow scope, 422 conflict) on the `update-branch` write itself is reported once per
  head and not retried — act per `reference/events.md`. `mergeable_state=dirty` is reported as
  `CONFLICTS` and never touched. The two reads that decide whether to even attempt a sync — the
  behind-by fetch and the approvals-for-sync fetch — go through the same retry as every other
  verdict-driving read: a failure there is never "not behind" or "0 approvals" (either would silently
  skip a real sync, or sync and dismiss an actually-approved PR); it prints `LOOKUP FAILED` instead and
  the sync attempt is skipped for that cycle, retried on the next one.
- The merge commit it lands is not reported again: the next poll recognises it (a two-parent `web-flow`
  commit whose first parent is the head it synced from) and tracks it silently — no `HEAD MOVED` after
  `SYNCED`. In bot mode the watcher then removes and re-adds `github.review_bot` as a requested reviewer
  itself (DELETE then POST, as `pr-merge.sh` does — a plain POST is a no-op on a merge-commit head), only
  once that commit is the head, since a request sent earlier would review a head about to be replaced.
  A failed re-request is a `RE-REQUEST … failed` line. Any other move — a push landing in between — is a
  normal `HEAD MOVED`, and the session re-requests as before.
- Consequences you own: a merge commit lands on the branch, invisibly to the session until its next push —
  the SKILL.md standing rule (`git fetch origin <branch>`, rebase onto it if ahead, `sign-queue --rebase`
  where commits are signed) is what catches this, not an event; CI re-runs on the PR.
  The sync never fires on an approved PR — on approval, `pr-merge.sh` / `update-branch` by hand is the path.
