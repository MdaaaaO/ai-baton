# Rules this encodes (verified on a strict-ruleset repo with a review bot)

- The repo blocks merge on any unresolved thread, even a non-blocking LOW one — reply and resolve every thread. A thread whose commenter
  approved after our reply is ours to resolve (`WORKSPACE.md` § Rules → Workflow & scope); one waiting on a
  named third party who has not approved stays open — it is the merge gate.
  Every reply and comment on your own PR ends with the footer line from the resolved-profile block already in
  your SessionStart context (`` footer: session `<name>` ``, the session that wrote it); once a compaction drops
  that block, or it never printed, fall back to `python3 $BATON/context-db/bin/kit_profile.py footer`. Reviews
  on someone else's PR (`pr-review`) carry no footer. Before posting a reply, write it to a
  file and run `python3 $BATON/context-db/bin/kit_profile.py public-text-check <file> --repo <o>/<r>` — exit 0 →
  post; exit 1 → rewrite the hits generically (never post the reply as is) and re-check; exit 3 → do not post —
  the env store could not be loaded, so the check did not run; fix the store or check the reply by hand before
  posting; a no-op when `<o>/<r>` is one of `tracker.repos` or not public.
- Strict required checks: a PR that is BEHIND main is refused; `PUT pulls/N/update-branch` first (GitHub-signed merge commit), then the bot must review the new head. **Do this yourself** — it works with the token `gh` runs with for every PR that does not touch `.github/workflows/**`. **Exception:** a PR carrying a workflow-file change gets 403 where that token lacks the `workflow` scope (a proxy-injected token usually does). Then rebase the worktree onto `origin/main` yourself and enqueue a `sign-queue` job with `--rebase` so the user's routine `make sign` pushes it; only if that is impractical hand them the one command for their own machine, `cd <abs repo> && gh pr update-branch <n>`. The merge itself never needs the user's machine: once `mergeable_state` is `clean`, `gh pr merge <n> --repo <o>/<r> --squash` goes through with the same token (`pr-merge.sh` runs the same command — no `--auto`).
- Squash footers: `pr-merge.sh` squashes with the repository's default message. A PR whose body ends in a
  conventional footer block (`BREAKING CHANGE: <step>`, after a blank line) in a repository whose default drops the
  body: once the gates hold, merge it by hand with the footer lines only, never the whole body —
  `gh pr merge <n> --repo <o>/<r> --squash --subject '<title> (#<n>)' --body-file <footers>` — or the footer, and with
  it the release's bump level and notes, never reaches the default branch.
- Re-request the bot with `DELETE` + `POST pulls/N/requested_reviewers` (the env config's `github.review_bot`). A run that finishes in ~20 s without posting = re-request again; `gh pr ready --undo && gh pr ready` is the fallback.
- Before an automated merge, test the bot verdict **on its own** — `bot-verdict.sh` prints a plain `green`/`yellow`/`red`/`none`, so `case "$v" in green) …` never risks matching a combined string against the human `reviewDecision` `APPROVED`, the bug the old ad hoc regex had to guard against.
- Do not watch PRs another session owns — one watcher per PR *across sessions*. Within your own session
  that is still one **process** for several PRs (the multi-PR form), not one Monitor each.

Source of truth for the scripts: this directory — whichever session improves them copies the change back here (no master session since 2026-09-12).

## pr-merge.sh phases

1. Wait for the review bot's **Assessment on the current head**, read from the review *object* (a green "PR
   Review" check is not a review).
2. If BEHIND, `update-branch`, then force a full bot review on the merge head with a DELETE + POST re-request of
   the bot as a requested reviewer — a plain `POST` alone is a no-op on a merge-commit head, and the draft-toggle
   fallback (`gh pr ready --undo && gh pr ready`) is visible to every other reviewer and cancels a
   `ready_for_review`-triggered run, so the script uses neither — then wait again.
3. Squash-merge when `CLEAN` + `reviewDecision APPROVED` + zero unresolved threads, **confirmed by re-reading the
   PR's `merged_at`** — a `gh pr merge` call that reports success but leaves `merged_at` unset is not read as
   merged.

Exit 0 = confirmed merged; exit 1 = a gate failed, the merge call failed, or `merged_at` came back unset (one-line
reason: head moved, open threads, not green, approval missing — read it and rerun after acting); exit 3 = gave up
(CLEAN + APPROVED never held within the deadline). The script squashes with the repository's default message; a
body ending in a `BREAKING CHANGE:` footer needs the hand merge in the squash-footers rule above.

