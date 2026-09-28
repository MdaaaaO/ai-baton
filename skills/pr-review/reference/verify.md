# Verify before you repeat — the axis-by-axis rules behind step 4

- Data claims (`systems.datalake` only; else mark them `unverified`) → run SQL (the datalake MCP); distribution checks beat existence checks; on token
  expiry ask the user to `/mcp` reauth. Check a timestamp column's actual type first — raw and modelled layers may type it differently.
  The **main session probes the token before spawning** (`select 1`; `reference/runner.md` § Spawn) so
  the runner never discovers it expired mid-review.
- CI state → `bundle.json.checks/status` as fetched; **never poll or read CI job logs** — report red/pending as such.
- Code/config baseline → `$CTX/base/<path>` (the file at the base sha, in the bundle); `gh api contents …?ref=`
  only for a file the PR did not touch; never the working tree.
- Convention claims → the documented spec (the schema/contract definitions the repo points
  to, dbt doc blocks, config reference), not a neighbouring precedent.
- Name the axis each ✅ covers ("logic verified against the manifest, not executed").
- Drop any candidate you cannot evidence; keep one line for the body if it is a natural question.

## Modelling PRs — the mandatory data pass

`dbt/models/**`, BI-consumed tables, warehouse scripts that reshape data — where `systems.datalake` is
on, the data pass is mandatory when the PR is complex (`--deep` triggers, a curated/serving-layer model,
a filter/join/grain change, or a backfill). Rerun the numbers yourself in the datalake (its SQL MCP),
never accept the author's: row count at the declared grain before vs after (`count(*)` vs `count(distinct
<key>)`), distribution of every added/changed column, NULL-rate delta, and a sample of keys whose values
flip. A PR body number you could not reproduce is a Verification-gap finding. If the token is expired,
the runner marks those rows `unverified` and the main session asks the user to `/mcp` reauth, then
re-spawns one re-verify runner (`reference/runner.md`). (owner decision, 2026-09-19.)
