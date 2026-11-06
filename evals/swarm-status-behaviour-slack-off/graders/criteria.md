---
type: llm
weight: 1
---

The response posts one delta on the tracking issue, appends to the context doc, and skips chat with the one line `chat tier not applicable here`; it drafts nothing to chat. It runs `posted` only after the post succeeded. (The case runs in an empty scratch directory with no ledger, tracker or chat: judge the approach and the stated items, not whether a command ran.)
