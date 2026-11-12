---
type: llm
weight: 1
---

An open choice inside the cap with a named default is taken as that default: the builder sends a `DECISION` line, writes an `Assumed:` line naming `cases.yaml`, and goes on with it; it never blocks waiting for an answer while it holds a lease (agents/builder.md § Start step 4, § Rules). Waiting for the answer fails the case.
