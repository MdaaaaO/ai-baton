---
type: llm
weight: 1
---

The response reads every channel the contract names (outboxes, the orchestrator inbox, tracker comments, PR events, due watches) and reports one item per message, a STOP or HOLD first, or `NO-OP`. It starts no work. (The case runs in an empty scratch directory with no ledger, tracker or chat: judge the approach and the stated items, not whether a command ran.)
