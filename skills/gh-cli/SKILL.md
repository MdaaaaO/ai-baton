---
name: gh-cli
description: "How to query GitHub with `gh` without the known traps: auth (native login vs a proxy token prefix), `--jq` has no `--arg`, search rate limits and caps, `reviewed-by`/`review-requested` semantics, review pagination, `--json` fields older gh lacks (`wait-checks.sh`), writes that silently fail. Load before any non-trivial `gh api`, `gh search` or `gh pr` work."
metadata:
  version: "10"
  updated: "2026-09-27"
  reviewed: "2026-09-25"
---

# gh-cli — querying GitHub with `gh`

Every trap below has cost a real session at least one wasted background run. Read § Traps
before writing a loop; copy the recipes in § Recipes rather than improvising.

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

| Trap | What happens | Do this instead |
|---|---|---|
| `gh api … --jq '…' --arg x y` | `unknown flag: --arg` — `--jq` is a bare filter, it takes no jq flags. In a loop this fails on **every** iteration and the output file stays empty while the error file grows to hundreds of KB (2026-09-18, twice in one session). | `gh api … \| jq -c --arg x "$x" '…'`. Check `wc -l` of the output **and** the error file before trusting a background job's result. |
| `gh api --paginate --jq` on `/reviews` or `/comments` | `--jq` runs **per page**; a filter like `[.[] \| …] \| length` returns one number per page, not a total. | `gh api --paginate … \| jq -s 'add // []' \| jq '…'`. |
| Reading reviews without `--paginate` | 30/page; a verdict on page 2 is invisible. | Always `--paginate` on `/reviews`, `/comments`, `/pulls/N/files`. |
| `gh search prs --json mergedAt` | `mergedAt` is not a supported field. | `gh search prs --merged --merged-at ">=YYYY-MM-DD" --json closedAt` (closedAt == mergedAt for merged PRs). |
| `reviewed-by:<login>` | Includes PRs the person **authored** (own review comments, bot runs they triggered). One login's count dropped by three quarters once own PRs were excluded. | Always pair with `-author:<login>` for "peer reviews given". |
| `review-requested:<login>` | Includes **team** requests via CODEOWNERS (e.g. `* @<org>/<team>`), so it inflates a personal queue (fourfold for one person). | `user-review-requested:<login>` for direct requests only; report both if the CODEOWNERS load matters. |
| Search API rate limit | 30 requests/min authenticated; a 90-cell sweep must pace itself or silently loses cells. | `sleep 2.2` between calls, in a background job; write `login\tkind\twindow\tcount` rows to a TSV and re-run only the missing cells. |
| Search result cap | 1,000 results per query; `--limit` above that is ignored. | Split by repo, by author or by date window. |
| Renamed / suspended user | `Invalid search query … users cannot be searched` for one login (seen once). | Fall back to repo-scoped PR lists filtered by `.user.login`; mark the cell as partial in the write-up. |
| `gh pr edit` | Applies **nothing** (Projects-classic GraphQL error) on every flag, exit 0. | REST: `gh api -X PATCH repos/o/r/pulls/N -f title=… -f body=…`, `POST issues/N/labels`; re-read afterwards. |
| Re-requesting an already-requested reviewer | Emits no event; the bot runs nothing. | Remove, then re-add: `DELETE pulls/N/requested_reviewers` then `POST`. |
| `gh api search/code` | Needs `org:` or `repo:` qualifier, and only indexes default branches. | `-f q="<term> org:<org>"`. |
| `contents` API on a directory | Returns a JSON array, not content; feeding it to `base64 -d` gives `invalid input`. | Check `type` first; `--jq .content \| base64 -d` only on a file. Piping a 404 body into another `gh api` call produces the "unsupported protocol scheme" error. |
| `--json` on `gh search prs` returns an **array** | `jq -r '.state'` on the top level fails with "Cannot index array". | `jq -r '.[] .state'`. |
| `gh pr checks <n> --json …` | `unknown flag: --json` on older gh (2.46, the Ubuntu/WSL apt package). Behind `2>/dev/null` the empty result read as "still pending" and a poll loop spun >10 min (2026-09-25). | `bash wait-checks.sh` (§ Waiting on checks and runs) — the REST check APIs work on every version. |
| Big loops in the foreground | The Bash tool kills the process group after ~10 min. | `run_in_background: true` for anything over ~100 calls; `setsid nohup` for anything that must outlive the tool call. |

## Recipes

Counting (cheap, one call, exact):
```sh
gh api -X GET search/issues -f q="author:<login> type:pr user:<org> is:merged merged:>=<since>" --jq .total_count
gh api -X GET search/issues -f q="reviewed-by:<login> -author:<login> type:pr user:<org> updated:>=<since>" --jq .total_count
```

Listing with fields (search, ≤1000, paged 100):
```sh
gh api -X GET search/issues -f q="…" -f per_page=100 --paginate \
  --jq '.items[] | "\(.repository_url|sub(".*/repos/";""))\t\(.number)\t\(.user.login)\t\(.created_at)"' > list.tsv
```
`--jq` per page is fine here because each row is independent.

Per-PR details in a loop (jq flags outside `gh`):
```sh
while IFS=$'\t' read -r repo num rest; do
  gh api "repos/$repo/pulls/$num" 2>>err.txt \
    | jq -c --arg r "$repo" '{repo:$r,n:.number,add:.additions,del:.deletions,files:.changed_files,merged:.merged_at}' >> sizes.jsonl
  gh api "repos/$repo/pulls/$num/reviews" --paginate 2>>err.txt \
    | jq -sc --arg n "$num" 'add // [] | {n:$n, states:[.[].state], reviewers:([.[].user.login]|unique)}' >> revs.jsonl
done < list.tsv
echo "rows=$(wc -l < sizes.jsonl) errs=$(wc -l < err.txt)"   # both, always
```

Paced search sweep (background):
```sh
ORG=$(python3 $BATON/context-db/bin/kit_profile.py get github.org)
for l in "${LOGINS[@]}"; do for w in <window-start-1> <window-start-2>; do
  n=$(gh api -X GET search/issues -f q="author:$l type:pr user:$ORG is:merged merged:>=$w" --jq .total_count 2>>err.txt)
  printf '%s\tauthored\t%s\t%s\n' "$l" "$w" "$n" >> cells.tsv; sleep 2.2
done; done
```
Afterwards: `awk -F'\t' '$4==""' cells.tsv` lists the cells to re-run.

Reading a file from a repo without cloning:
```sh
gh api repos/<org>/<repo>/contents/<path> --jq .content | base64 -d
gh api repos/<org>/<repo>/contents/<dir> --jq '.[] | .path'        # directory listing
```

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
- Numbers go in a table in the reply, never in prose (`CLAUDE.md` § Writing for the user).

## Related

Skills: `pr-open` (labels, review-request draft, diagrams), `pr-watch` (event watch), `sign-queue`
(commits/pushes — never from here), `self-assessment` (the three GitHub sweeps).
The lessons behind the traps (what went wrong, the rule each one left): `reference/lessons.md` beside this file.
