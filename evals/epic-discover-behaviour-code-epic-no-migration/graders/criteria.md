---
type: llm
weight: 1
---

The epic changes code and moves no data, so the verification question recommends the code-change profile and does not offer a data-migration profile at all (custom may be offered). The drafted plan's gates follow the code-change template (tests that fail on the default branch, approved review and green CI, every `DW-<n>` covered, the miss check and operator brief); no pipeline block, since no dbt model or DAG changes.
