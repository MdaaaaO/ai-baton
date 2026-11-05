---
type: llm
weight: 1
---

The builder agent returns grammar lines only (agents/builder.md § Return). The final message is exactly the lines it owes, such as `READY 42 <head7>` and any `Assumed:` line, and nothing else. A near miss: a free-text narrative, a progress recap or a greeting in the message fails; detail belongs in the named scratch files, which the message may only point at by the grammar's lines.
