# Arming a watch — arguments, state, identity, no-bot mode

Read on demand from `SKILL.md` § Arm a watch.

## Arguments and state

`pr-watch.sh <owner/repo> <pr_number> <head_sha_prefix> [<pr_number> <head_sha_prefix> …]` — all PRs of one
repo in one process. The single-PR form (`… <owner>/<repo> <pr> <sha>`) works unchanged; a second repo needs a
second Monitor. Per-PR state lives in `${TMPDIR:-/tmp}/pr-watch-<owner-repo>-<pr>/` (one file per variable). A
merged/closed PR prints its line and drops out of the round-robin; the process exits when the last one is gone.

Re-arming with the identical call is silent: nothing already reported is replayed, and a head the watcher already
followed is kept, not reported again — an expiry costs its one wake-up and nothing more
(`reference/cost-controls.md`, the silent re-arm bullet).

## Always `bash <script>`

Run the script as `bash $BATON/skills/pr-watch/pr-watch.sh …`, never by its path alone: the file's execute bit is
not reliable on every mount (exit 126 = it was forgotten). The script itself is POSIX-safe, so a watcher started
with `sh` does not crash on a bash-only expansion either.

## Token and identity

The script applies `github.sandbox_token_prefix` itself (a placeholder token in a sandbox whose proxy injects
credentials, nothing where `gh` is logged in). Set `PR_WATCH_SELF=<your GitHub login>` if it is not
`$WORKSPACE_GITHUB_LOGIN` (the user's login); events by that login are dropped as your own. Neither set and
`gh api user` fails (or names nobody): startup prints an `ERROR … startup:` line and exits 2 — it never falls back
to a placeholder identity, since that would stop filtering this session's own replies and turn every one of them
into a new event.

## No bot configured

`github.review_bot` empty is the default on most machines. The watcher prints which mode it is in on the first
line (`pr-watch: bot mode …` / `pr-watch: no-bot mode … — gating on CI + human review only`, stderr) and never
polls for a bot Assessment: no `BOT REVIEW` line, no wait, no paginated `reviews` fetch for it every cycle.
`pr-merge.sh` does the same on its own first line — the merge gate becomes `mergeStateStatus CLEAN` +
`reviewDecision APPROVED` + zero unresolved threads, same as the bot-mode phase 3 loop.
