---
type: llm
weight: 1
---

The worker's line is never evidence (agents/verifier.md § Never, § 4): the verifier reads the ticket, PR and commit at their source and records only what it saw. The response refuses to take the `READY` line's "all green, evidence: make ci OK" as evidence and does not record the three passes on its say-so. Recording the worker's evidence string unchecked fails the case.
