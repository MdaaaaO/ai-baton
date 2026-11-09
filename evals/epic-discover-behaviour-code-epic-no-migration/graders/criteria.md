---
type: llm
weight: 1
---

The epic changes code and moves no data, so the verification question recommends the code-change template and does not offer a data-migration template at all (custom may be offered). The drafted plan holds the code-change template's four gates (tests that fail on the default branch, approved review and green CI, every `DW-<n>` covered, the miss check and operator brief); gates the session adds for this epic in the same format are fine. No pipeline block, since no dbt model or DAG changes.
