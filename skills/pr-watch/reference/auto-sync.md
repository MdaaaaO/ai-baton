# Auto-sync with the base branch (since 2026-09-21)

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
  (60-min Monitor cap) does not restart the clock at zero. `PR_WATCH_SYNC=0` turns it off (e.g. a PR
  someone is mid-review on, or a branch the user is about to force-push).
- A failure (403 workflow scope, 422 conflict) is reported once per head and not retried — act per
  `reference/events.md`. `mergeable_state=dirty` is reported as `CONFLICTS` and never touched.
- Consequences you own: a merge commit lands on the branch, so any further push from the worktree needs
  `sign-queue --rebase`; the bot must review the new head (`HEAD MOVED` rule); CI re-runs on the PR.
  The sync never fires on an approved PR — on approval, `pr-merge.sh` / `update-branch` by hand is the path.
