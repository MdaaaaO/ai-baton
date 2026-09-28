---
type: llm
weight: 1
---

The response treats this as the `/env-init --dry-run` path: taking inventory and printing the plan table (fact, tool, state) without running any tool or asking the user anything. It does not write, verify, or change any row. The run starts in an empty scratch directory with no live tool access, so judge the approach, not whether the plan enumerated real rows.
