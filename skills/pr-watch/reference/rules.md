# Rules this encodes (verified on a strict-ruleset repo with a review bot)

- The repo blocks merge on any unresolved thread, even a non-blocking LOW one — reply and resolve every thread.
  Every reply and comment on your own PR ends with `python3 $BATON/context-db/bin/kit_profile.py footer` (the session
  that wrote it); reviews on someone else's PR (`pr-review`) carry no footer.
- Strict required checks: a PR that is BEHIND main is refused; `PUT pulls/N/update-branch` first (GitHub-signed merge commit), then the bot must review the new head. **Do this yourself** — it works with the token `gh` runs with for every PR that does not touch `.github/workflows/**`. **Exception:** a PR carrying a workflow-file change gets 403 where that token lacks the `workflow` scope (a proxy-injected token usually does). Then rebase the worktree onto `origin/main` yourself and enqueue a `sign-queue` job with `--rebase` so the user's routine `make sign` pushes it; only if that is impractical hand them the one command for their own machine, `cd <abs repo> && gh pr update-branch <n>`. The merge itself never needs the user's machine: once `mergeable_state` is `clean`, `gh pr merge <n> --repo <o>/<r> --squash` goes through with the same token (`pr-merge.sh` runs the same command — no `--auto`).
- Re-request the bot with `DELETE` + `POST pulls/N/requested_reviewers` (the env config's `github.review_bot`). A run that finishes in ~20 s without posting = re-request again; `gh pr ready --undo && gh pr ready` is the fallback.
- Before an automated merge, test the bot verdict **on its own** — `bot-verdict.sh` prints a plain `green`/`yellow`/`red`/`none`, so `case "$v" in green) …` never risks matching a combined string against the human `reviewDecision` `APPROVED`, the bug the old ad hoc regex had to guard against.
- Do not watch PRs another session owns — one watcher per PR *across sessions*. Within your own session
  that is still one **process** for several PRs (the multi-PR form), not one Monitor each.

Source of truth for the scripts: this directory — whichever session improves them copies the change back here (no master session since 2026-09-12).
