---
name: dbt-sqlfluff-fixes
description: "Recognise and fix the sqlfluff violations that keep recurring in dbt models: WHERE/ON line breaks, OVER-clause-in-OR-chain indent fights, short aliases, AS alignment, nested CASE. Use when writing or editing a dbt .sql model, or when a PR's lint-models check fails and you need the exact reviewdog findings fixed in one CI round."
compatibility: "Designed for Claude Code; needs dbt (systems.*)"
metadata:
  version: "6"
  updated: "2026-09-26"
  reviewed: "2026-09-24"
  requires: "dbt"
user-invocable: true
---

# dbt/sqlfluff Fixes

Where the session's machine has no dbt target with a live datalake connection (`~/.dbt/profiles.yml`
absent — a sandbox, a fresh clone), `sqlfluff` cannot compile the models' Jinja locally — the dbt
templater needs the connection — so every lint fix round-trips through CI, which is slow. Check once:
`command -v sqlfluff && test -f ~/.dbt/profiles.yml`; where both hold, lint locally and skip the round
trip. This skill exists to front-load pattern recognition so a fix is right the first time, and to make
reading CI's actual findings fast when it isn't.

## Reading the real findings instead of guessing

Don't try to predict sqlfluff's output by eye for anything beyond the simple cases
below — some of its indent rules (see LT02 note) are genuinely inconsistent in ways
that aren't worth reverse-engineering. Get the exact findings instead:

```bash
# Apply github.sandbox_token_prefix first where one is set: eval "$(python3 $BATON/context-db/bin/kit_profile.py gh-env)"
# From a failed lint-models job (get the job id from `gh pr checks <PR>`):
gh api repos/<owner/repo>/actions/jobs/<job_id>/logs \
  | grep -E "LT0|AL0|CP0|RF0|ST0" | grep -v "level=INFO\|reviewdog:"
```

or read the reviewdog PR review comments directly:

```bash
gh api repos/<owner/repo>/pulls/<PR>/comments \
  --jq '.[] | select(.user.login=="github-actions[bot]") | "\(.path):\(.line): \(.body)"'
```

Each finding gives you the exact file, line, rule code, and a one-line description —
usually enough to apply the matching fix below without needing to see surrounding
context.

## Recurring violations and their fixes

### LT02 — "Expected line break and indent of N spaces before '\<token\>'"

Almost always means: the keyword and its first condition are on the same line, and
sqlfluff wants the keyword alone on its own line with the condition(s) indented
underneath. Applies to both `WHERE` and multi-condition `ON`:

```sql
-- Before (flagged)
WHERE change_branch = 'allowlist_rule'
    AND rule_id IS NOT NULL

-- After
WHERE
    change_branch = 'allowlist_rule'
    AND rule_id IS NOT NULL
```

```sql
-- Before (flagged)
LEFT JOIN rule_field_changes AS rfld
    ON rfld.event_id = bevt.event_id
    AND rfld.policy_id = bevt.policy_id

-- After
LEFT JOIN rule_field_changes AS rfld
    ON
        rfld.event_id = bevt.event_id
        AND rfld.policy_id = bevt.policy_id
```

### LT02 — `OVER (...)` inside an OR-chain: don't fight it, restructure instead

A boolean expression like a gaps-and-islands `starts_new_version` check with several
`LAG(x) OVER (...) IS DISTINCT FROM LAG(y) OVER (...) OR ...` conditions gets
flagged inconsistently — sqlfluff has demanded different indents for near-identical
`OVER (...)` blocks in the same OR-chain, sometimes flagging 5 of 6 nearly-identical
occurrences and not the first. This is not worth guessing at twice. Restructure into
two CTEs instead, the pattern most linted dbt codebases already use:

1. A `lagged_values` CTE: one `LAG(col) OVER (PARTITION BY ... ORDER BY ...) AS
   prev_col` **per line**, no inline boolean logic at all — the same plain style
   that lints clean for `LAST_VALUE` forward-fill columns.
2. The actual boundary-detection CTE: plain column-vs-`prev_column` comparisons with
   `IS DISTINCT FROM`, chained with `OR` — **no windowing in this CTE at all**, since
   it already happened in step 1.

Same logic, zero `OVER (...)` left sitting inside an `OR` chain to format. Don't try
a named `WINDOW` clause to shorten repeated `OVER (...)` blocks instead — some datalake dialects
require `OVER (window_name)` with parens around the reference (unlike Postgres'
bare `OVER window_name`), which is an easy syntax error to introduce while trying to
fix a lint nit.

### AL06 — "Aliases should be at least 4 character(s) long"

The minimum alias length is a repo setting (`aliasing.length` in `.sqlfluff`, 4 is common), and a repo's
`CLAUDE.md` often adds "descriptive enough to be understood without context" — so the fix isn't
just padding to 4 characters, it's picking a real abbreviation. Read the repo's existing models
first and follow their precedent: first letters of each word (`oitm` for `order_items`,
`cust` for `customers`) or a recognizable truncation (`elem` for `element`), not an
opaque 4-letter code.

### LT01/CP01 — manual `AS`-alignment padding

Don't hand-align a column list's `AS` keywords with extra spaces for a tidy visual
column. Single space before `AS`, nothing more, even if it makes the list look
ragged.

### Nested `CASE` inside a `THEN` (or similarly nested conditionals)

Flatten into one `CASE` whose `WHEN` clauses combine the conditions directly (`WHEN a
AND b THEN ...`) rather than nesting a second `CASE` inside the first `THEN`. Same
result, no nesting for sqlfluff to complain about.

## General rules to write correctly the first time (verify them in the repo's `CLAUDE.md` / `.sqlfluff`)

- Keywords uppercase, identifiers lowercase.
- Explicit table and column aliasing required, minimum 4 characters, descriptive
  (see AL06 above).
- Bare `SELECT *` is flagged outside staging models — except a repo's own final-CTE idiom
  (`SELECT final.* …` at the end of a model) where the repo establishes one.
- Max line length 120.
- Run `sqlfluff` from the **repo root**, not from `dbt/` — `uv run sqlfluff lint
  dbt/models/path/to/file.sql` — whether you run it yourself (a machine with a dbt target) or
  hand the command to the user.

## A correctness bug that looks like a lint fix: `LIKE` with `_` or `%`

Not a sqlfluff rule, but shows up in the same review pass and is easy to get wrong
twice in a row. `_` and `%` are wildcards inside `LIKE` — `LIKE '%_%'` unescaped
matches *everything*, silently a no-op, not "contains an underscore." The fix needs
an explicit `ESCAPE` clause — but **don't use backslash as the escape character**:
`LIKE '%\_%' ESCAPE '\'` breaks the string-literal parsing of dialects where
backslash is already the escape character inside a string literal, so the
string never closes and you get a cascade of unrelated-looking syntax errors further
down the file. Use a different character instead: `LIKE '%!_%' ESCAPE '!'`.

## Quick checklist when `lint-models` fails on a PR

1. Pull the exact findings (see "Reading the real findings" above) — don't guess.
2. Match each finding's rule code against the sections above.
3. Fix all of them in one pass, then re-read the whole changed CTE once by eye for
   the OVER-in-OR-chain trap even if sqlfluff didn't flag it yet — it's the one rule
   in this list that's flagged inconsistently, so a clean run on one push doesn't
   guarantee the next one is too if the surrounding code shifts.
4. Push once. If it still fails, pull fresh findings rather than assuming you know
   what changed.
