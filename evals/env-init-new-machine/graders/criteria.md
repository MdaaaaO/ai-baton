---
type: llm
weight: 1
---

The response treats this as running env-init end to end: taking inventory of missing facts, printing a plan, running the named discovery tool per fact, verifying and writing results back with provenance, and batching whatever no tool can settle into one question round. It does not guess a value or infer one from a similar id. The run starts in an empty scratch directory with no live tool access, so judge the approach, not whether every fact actually resolved.
