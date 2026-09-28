# dbt-sqlfluff-fixes

Recognises the sqlfluff violations that recur in dbt models and fixes them from the real reviewdog findings,
so a failing `lint-models` check is green in one CI round.

**Needs:** `systems.dbt` (the environment has dbt models and a sqlfluff lint check on their PRs). Reads the
failed job's log through `gh` (with `github.sandbox_token_prefix` applied where one is set); no env fact rows.

**Without it:** `dbt-sqlfluff-fixes: not applicable here — dbt is false`, one line, stop — a plain SQL
file is edited without this skill's rules.

**Example:**

> **user:** the PR's lint-models check is red again
>
> **claude:** reads the exact findings from the job log, fixes each (WHERE/ON line breaks, OVER-clause indent,
> short aliases, AS alignment, nested CASE), re-runs the linter locally where available, and hands back one commit.
