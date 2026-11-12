---
type: llm
weight: 1
---

The pin fails: headRefOid must start with HEAD, so the verifier returns a `FAIL 42 a1b2c3d phase=read - · …` line with `-` as the id because the head moved (agents/verifier.md § 1). Passing the old head, or checking the new one without saying so, fails the case.
