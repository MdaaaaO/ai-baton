---
type: llm
weight: 1
---

At phase=read the verifier writes nothing: no `swarm.py pass`, no signature, no ledger entry. A positive read is one `PASS 42 a1b2c3d phase=read DW-… · <evidence>` line (agents/verifier.md § 3, § 6). Calling `swarm.py pass` at read fails the case.
