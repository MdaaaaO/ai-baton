---
type: llm
weight: 1
---

The response treats this as one orchestrator tick on the epic: it starts from the task ledger (`swarm.py tick`), not from memory, and walks the tick in the contract's order (read, inbox, decisions, assign, stalls, budget, integrate, verify, digest, flush). It does not start building a ticket itself. (The Skill call itself is `fired.md`'s job; grade the outcome here. A case runs in an empty scratch directory with no ledger or tracker, so judge the approach, not whether a call succeeded.)
