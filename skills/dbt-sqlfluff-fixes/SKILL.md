---
name: dbt-sqlfluff-fixes
description: "Recognise and fix the sqlfluff violations that keep recurring in dbt models: WHERE/ON line breaks, OVER-clause-in-OR-chain indent fights, short aliases, AS alignment, nested CASE. Use when writing or editing a dbt .sql model, or when a PR's lint-models check fails and you need the exact reviewdog findings fixed in one CI round."
compatibility: "Designed for Claude Code; needs dbt (systems.*)"
metadata:
  version: "8"
  updated: "2026-09-28"
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

> On a machine where `dbt` is false, print `dbt-sqlfluff-fixes: not applicable here — dbt is false` and stop.

## Reading the real findings instead of guessing

Don't try to predict sqlfluff's output by eye for anything beyond the simple cases in
`reference/violations.md` — some of its indent rules (see the LT02 note there) are genuinely
inconsistent in ways that aren't worth reverse-engineering. Get the exact findings instead:

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
usually enough to apply the matching fix from `reference/violations.md` without needing
to see surrounding context.

## Recurring violations and their fixes

Five patterns recur: LT02 (keyword-then-condition on one line — put the keyword alone, indent the
condition(s) underneath, for both `WHERE` and multi-condition `ON`); LT02 on an `OVER (...)` inside an
OR-chain (flagged inconsistently — restructure into a `lagged_values` CTE plus a plain
comparison CTE instead of fighting the indent); AL06 (alias below the repo's minimum length — pick a
real abbreviation from the repo's own precedent, not padding); LT01/CP01 (hand-aligned `AS` padding —
single space only); and a nested `CASE` inside a `THEN` (flatten into one `CASE` with combined `WHEN`
conditions). Worked before/after examples and the reasoning for each: `reference/violations.md`.

## General rules to write correctly the first time (verify them in the repo's `CLAUDE.md` / `.sqlfluff`)

- Keywords uppercase, identifiers lowercase.
- Explicit table and column aliasing required, minimum 4 characters, descriptive
  (`reference/violations.md` § AL06).
- Bare `SELECT *` is flagged outside staging models — except a repo's own final-CTE idiom
  (`SELECT final.* …` at the end of a model) where the repo establishes one.
- Max line length 120.
- Run `sqlfluff` from the **repo root**, not from `dbt/` — `uv run sqlfluff lint
  dbt/models/path/to/file.sql` — whether you run it yourself (a machine with a dbt target) or
  hand the command to the user.

## A correctness bug that looks like a lint fix: `LIKE` with `_` or `%`

Not a sqlfluff rule, but shows up in the same review pass and is easy to get wrong twice in a row —
unescaped `_`/`%` wildcards silently match everything; the fix and the escape-character trap:
`reference/violations.md`.

## Quick checklist when `lint-models` fails on a PR

1. Pull the exact findings (see "Reading the real findings" above) — don't guess.
2. Match each finding's rule code against `reference/violations.md`.
3. Fix all of them in one pass, then re-read the whole changed CTE once by eye for
   the OVER-in-OR-chain trap even if sqlfluff didn't flag it yet — it's the one rule
   in this list that's flagged inconsistently, so a clean run on one push doesn't
   guarantee the next one is too if the surrounding code shifts.
4. Push once. If it still fails, pull fresh findings rather than assuming you know
   what changed.
