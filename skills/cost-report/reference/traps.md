# Traps

- Org mode has no default schema: `cost.columns` (all eight) and `cost.filters` come from `DESCRIBE TABLE`
  of *this* warehouse, set once per environment. A default would build SQL for another environment's table.
- `propose-columns` proposes, it never writes: a role it scores `?` is a real ambiguity (two token columns,
  none named for cache reads) — confirm it with the user, do not pick the first candidate.

## Column mapping — `DESCRIBE TABLE` → `cost.columns`, proposed, confirmed once

`cost_report.py sql` exits 2 while any `cost.columns.<role>` is unset. Then, once per environment:

1. `kb.py discover cost.columns` prints the plan — the `DESCRIBE TABLE <cost.spend_table>` call for the
   warehouse tool (`datalake.tool sql`) and the write line. Run the call; save the result as a JSON array of
   row objects (`describe.json` — the warehouse's `name`/`type` rows; any vendor's shape with a column-name
   field works).
2. `python3 $BATON/skills/cost-report/cost_report.py propose-columns --describe describe.json` → one table
   `role | column | score | alternatives` and the exact `kb.py config-set cost.columns '{…}'` line. A role
   scores `ok` when exactly one column matches its vocabulary (`usage_date`/`day` for `date`,
   `cache_read_tokens`/`cache_read_input_tokens` for `cache_read`, …) and `?` when none or several do; the
   line carries `<?>` for those and the script exits 3.
3. Exit 0 → run the line as printed. Exit 3 → **one** `AskUserQuestion` listing each `?` role with its
   candidates (never one question per role), fill the `<?>` slots, run the line, then `cost_report.py sql`
   again — the exit-2 list should now name only `cost.filters` / `cost.email`, which stay the user's
   (`kb.py config-set cost '{…}'`; a filter predicate is not discoverable).

## Other traps

- `gh search prs` caps at 1000 rows and 30 calls/min; the script queries one calendar month per scope and
  **exits non-zero on a full page** (a truncated count would inflate `$/PR`). `--since` defaults to the
  earliest `cost.phases` start, else 365 days back — pass it explicitly for a longer history. gh ≥ 2.4x
  reports merged PRs as state `merged` (older versions `closed`); both are handled. The `github.sandbox_token_prefix`
  placeholder is honoured via `kit_profile.py gh-env`.
- The two printed queries use no engine-specific function (no `DATE_TRUNC`/`TO_CHAR` week-format, no
  `DAYOFWEEKISO`): ISO-week bucketing and the weekdays-only filter both happen in `ingest`, in Python, from
  a plain daily `GROUP BY`. A warehouse rejecting a plain `CAST(… AS DATE)` or `SUM`/`GROUP BY` would be
  unusual — `ingest` only needs the column aliases, not the dialect.
- MCP query results that exceed the tool's size limit come back truncated; page by month in the SQL
  (`BETWEEN`) and concatenate the JSON arrays before `ingest`.
- Transcript timestamps are UTC; `collect-private` buckets them into the owner's calendar day (`kit_profile.tz`,
  reported in the Basis block) so a late-evening session does not land on the next day or on a weekend. An unknown
  `WORKSPACE_TZ` falls back to UTC and the Basis says so. **The day boundary differs between modes:** org mode takes
  the warehouse's date column as it is (usually UTC), private mode the owner's zone — the same evening's work can
  sit on different days in the two reports, so never compare a private and an org bucket day by day.
- Config values are validated before they reach SQL: table and column names must be plain dotted identifiers,
  `--since/--until` must be `YYYY-MM-DD`, the email is quote-escaped; `cost.filters` is free SQL by design —
  it is the environment owner's own predicate, not user input.
- `work-prs` never runs one `gh pr view` per PR: where an older gh reports `closed` for merged PRs, one extra
  `is:merged` search per scope-month settles the merge date. `work-prs`/`work-tickets` turn GitHub's UTC
  `createdAt`/`closedAt` into the owner's calendar day the same way `collect-private` does, so a PR merged or
  ticket closed near local midnight lands on the day its spend does, not on whichever day UTC happened to show.
- A transcript estimate is high for cache-heavy sessions relative to a negotiated rate and low when
  the account is billed for retries; treat private-mode $ as ±20%, and lean on in/out and cheap%,
  which need no prices at all.
