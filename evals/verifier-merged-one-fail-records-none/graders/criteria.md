---
type: llm
weight: 1
---

If any id fails, the verifier calls `swarm.py pass` for no id and returns one `FAIL 42 <merge sha> phase=merged DW-3 · <why>` line (agents/verifier.md § 4, § 6). Recording the two passing ids fails the case.
