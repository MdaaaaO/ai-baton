---
type: llm
weight: 1
---

The response checks whether a digest is due, renders it, and posts it as one delta on the tracking issue and into the context doc; it does not post twice. (The case runs in an empty scratch directory with no ledger, tracker or chat: judge the approach and the stated items, not whether a command ran.)
