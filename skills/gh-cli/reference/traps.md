# Traps (each one has happened)

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
| `gh pr checks <n> --json …` | `unknown flag: --json` on older gh (2.46, the Ubuntu/WSL apt package). Behind `2>/dev/null` the empty result read as "still pending" and a poll loop spun >10 min (2026-09-25). | `bash wait-checks.sh` (`SKILL.md` § Waiting on checks and runs) — the REST check APIs work on every version. |
| Big loops in the foreground | The Bash tool kills the process group after ~10 min. | `run_in_background: true` for anything over ~100 calls; `setsid nohup` for anything that must outlive the tool call. |
