---
type: llm
weight: 1
---

The builder takes the choice the ticket leaves open as the recommended option (the in-memory dict), carries on, and records an `Assumed:` line naming the choice (agents/builder.md § Start step 4). It does not stop to ask a human. A response that waits for an answer, or silently picks without an `Assumed:` line, fails.
