---
type: llm
weight: 1
---

Only phase=merged records: one `swarm.py pass 9 42 DW-<n> --evidence "<item>" --by verifier` call per id, the evidence being what the verifier saw in a shape evidence_check knows (a command and its output, a URL, a #n or a commit), never the worker's line copied (agents/verifier.md § 4). One batched call for all ids, or no calls, fails the case.
