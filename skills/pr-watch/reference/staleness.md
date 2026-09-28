# Staleness rules — why the scripts look the way they do

- **Paginate every listing.** `pulls/<n>/reviews`, `pulls/<n>/comments`, `issues/<n>/comments` return
  30 items per page. A PR with ten bot rounds has 40+ reviews, so a bare call never sees the newest
  verdict and a waiter sits "waiting for the bot" forever while the bot has already approved. Both scripts use `gh api --paginate "...?per_page=100"`; keep it that way in any new
  query, including one-off checks in the terminal.
- **Verdict = review object on the final head sha**, never a check run, never a review on an older
  head (`dismiss_stale_reviews` is off, so old approvals linger and look current). `bot-verdict.sh
  <owner/repo> <pr> <head>` is the one place this lookup lives — it replaced five hand-rolled versions
  of the same regex (fetch-context.sh, pr-scan.sh, pr-watch.sh, pr-merge.sh, trivial-check.py) that had
  started to drift; it prints `green`/`yellow`/`red`/`none` (exit 0), exits 2 when no bot is configured
  (never treat that as a failure — it is the no-bot branch in `SKILL.md`), and exits 1 with `gh`'s error on
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
