# 4b. Stakeholder impact — building the consumer map

Build the consumer map of every model/column the PR changes and write it to `$CTX/impact.json`
(`[{consumer, kind: dbt|bi|alert|export|other, owner, what_changes, rows_or_values_affected}]`):
- **dbt**: `grep -rn "ref('<model>')"` under `dbt/models` at the PR head (`gh api contents …?ref=`, the
  checkout is stale) + `exposures:` in the yml; note which of them select the changed columns.
- **BI tool** (`systems.datalake` only): the BI tool's MCP search for the model or table name, where one is connected — which
  workbooks/dashboards read it and who owns them. Skip with a `NOTE` line if the MCP is unavailable; never guess.
- **Alerts / exports**: alerting models, customer-export factories, reverse-ETL configs — anything that turns the column into a message or a file someone receives.
- Quantify: the SQL from step 4 gives "how many rows / which values change for consumer X".

Then: a change that alters what a known consumer reads and the PR body does not say so → **Stakeholder
impact** finding (`scope.md`), and the sheet's `NOTE` gets one `IMPACT:` line (consumers, owners, size).
No consumers found is also a result — record it in `impact.json` so the KB gains the consumer map (step 8).
