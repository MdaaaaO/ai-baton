---
name: gh-cli
description: "Querying GitHub with `gh` without the known traps: auth (native vs proxy token), `--jq`/`--arg`, search rate limits, pagination, old-gh `--json` gaps, silent write failures, and checks/runs waits without a hand-rolled loop. Use when doing non-trivial `gh api`/`search`/`pr` calls or CI/comment waits."
metadata:
  version: "16"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
  facts: "github.org,github.sandbox_token_prefix"
---

# gh-cli — querying GitHub with `gh`

Every trap in `reference/traps.md` has cost a real session at least one wasted background run. Read it
before writing a loop; copy the recipes in `reference/recipes.md` rather than improvising.

## Auth & environment

- **`<org>` below is this environment's GitHub org** (`python3 $BATON/context-db/bin/kit_profile.py get github.org`);
  the bot logins to exclude are `github.bots`, the review bot is `github.review_bot`. Never hardcode either.

- **Token: `github.sandbox_token_prefix`** (`kit_profile.py get github.sandbox_token_prefix`). Where a
  sandbox proxy injects the real token at the network layer, it holds a placeholder `NAME=value` —
  `gh` only needs a non-empty token to stop prompting, and `gh auth status` saying "not logged in" is
  expected there. Apply it with `eval "$(python3 $BATON/context-db/bin/kit_profile.py gh-env)"` (shell)
  or `kit_profile.gh_env()` (Python) — the kit's scripts already do. **Empty = `gh` is logged in
  natively: call it bare** — a placeholder token on such a host overrides the real login and every call
  answers 401. **A native login wins over the prefix**: where `gh auth token` succeeds without any token
  variable (keyring or `hosts.yml`), the kit's helpers leave the prefix off even when it is set, so a host
  sharing the store with a sandbox keeps its real login. Never hardcode a token value in a command.
- **Never print the token or `ps -o cmd`** — the Bash wrapper puts exports in argv, so a full command
  listing shows every exported secret. List processes with `comm` only.
- **Scope limits of a proxy-injected token:** where `gh` works through a token-injecting proxy (a sandbox),
  the token usually lacks the `workflow` scope, so `PUT pulls/N/update-branch` is 403 when the PR touches
  `.github/workflows/**`; that one step then runs on the user's machine (skill `pr-watch` § Rules), the
  later `gh pr merge` works with the same token.
- **Foreground `sleep` is blocked** by the harness. Anything that must pace itself (search
  sweeps) runs with `run_in_background: true`; poll its output file, don't re-run it.
- **Scratch files go to `$(python3 $BATON/context-db/bin/kit_profile.py scratch)`** (per session, exists on every machine —
  a job-directory variable one kind of machine exports is unset on the others), never a bare `/tmp` (shared between sessions) and never the
  transcript (a 100-row JSON dump is re-billed every turn).

## Traps (each one has happened)

Sixteen recurring traps — `--jq` flags, unpaginated reviews, search-field/rate/result-cap surprises,
`reviewed-by`/`review-requested` scope, `gh pr edit` applying nothing, re-request no-ops, the
`contents` API on a directory, older-gh `--json` gaps, foreground loop timeouts — each with what
happens and the fix: `reference/traps.md`.

## Recipes

Copy-paste shapes for counting, listing with fields, per-PR detail loops, a paced background sweep and
reading a file without cloning (exact `jq`/`gh` flags, where `--jq` per-page is safe vs. where it silently
undercounts): `reference/recipes.md`.

## Waiting on checks and runs

Never hand-roll a `until …; do sleep; done` over `gh`. A wait has three outcomes plus a deadline, and a
failed query is none of them — it must end the wait loudly, never read as "not ready yet"
(WORKSPACE.md § Verification: a swallowed error is not a negative result). Run waits with
`run_in_background: true` so the harness notifies on exit.

| Waiting for | Use | Exit |
|---|---|---|
| Every check on a PR head / commit | `bash $BATON/skills/gh-cli/wait-checks.sh <owner/repo> <pr\|sha> [timeout=900] [interval=20]` — check runs + suites + legacy statuses via REST, portable; done only when the finished set holds for two polls | 0 passed · 1 a check failed · 2 bad args / query failed (stderr) · 124 deadline |
| One workflow run you know the id of (e.g. an `@claude review` mention run — it runs on the default branch, not the PR head) | `timeout 900 gh run watch <run-id> --exit-status` — find the id with `gh run list -R <o/r> --workflow <file> -L 3` and take the run created just after your trigger (`createdAt` ≥ your comment's `created_at`) whose jobs did not skip — not "the latest `issue_comment` run": the bot's own reply comment starts one more run that skips | 0 success · non-zero failed · 124 deadline |
| A new comment or review | count **all pages** and keep the exit status of `gh`, not of a pipe: `ids=$(gh api --paginate "repos/<o/r>/issues/<n>/comments?per_page=100" --jq '.[].id') \|\| exit 2; n=$(printf '%s' "$ids" \| grep -c .)` (`pulls/<n>/reviews`, `pulls/<n>/comments` for reviews / inline comments); poll until `n` grows, same deadline. A bare `--jq length` stops at 30 (one page); `gh … \| wc -l \|\| exit 2` never exits (the pipe's status is `wc`'s). | — |

Rules for any other loop: check the command's exit status separately from its output
(`out=$(gh …) || { echo "query failed" >&2; exit 2; }`); never `2>/dev/null` a query whose empty output
decides whether the loop continues; give every loop a deadline. `gh pr checks <n>` (plain table) and
`--watch` exist on every version but have no deadline and no machine-readable form on old gh — prefer
the script.

## Interpreting the numbers (before they go to the user)

- **Count ≠ impact.** Always pair a PR count with size (additions/deletions/files) and a
  window that is equal for everyone (the user's own start date is the fair floor — never a different one per person).
- **Exclude bots** (the env config's `github.bots` list + `github.review_bot`) from author counts, and say so.
- **Agent-drafted reviews exist**: reviews whose body carries `<!-- gh-review-snapshot:v1 … -->`
  come from an agent that drafted the review for its author. Count them, but label them.
- **Repo scope matters**: a four-repo count can halve someone whose work is cross-repo; run the
  org-wide (`user:<org>`) count next to it.
- Numbers go in a table in the reply, never in prose (`WORKSPACE.md` § Rules).

## Related

Skills: `pr-open` (labels, review-request draft, diagrams), `pr-watch` (event watch), `sign-queue`
(commits/pushes — never from here), `self-assessment` (the three GitHub sweeps).
The lessons behind the traps (what went wrong, the rule each one left): `reference/lessons.md` beside this file.
