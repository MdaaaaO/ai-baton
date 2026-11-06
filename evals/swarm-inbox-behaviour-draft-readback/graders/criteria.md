---
type: llm
weight: 1
---

The response returns three draft items: D1 `draft-sent`, D2 `draft-edited` (sent with edits), D3 `draft-pending` (still pending). It returns a `CLOSE-DRAFT` trailer for D1 and D2 only (outcomes sent and sent-edited), none for D3, and runs no write itself. (The case runs in an empty scratch directory with no ledger, tracker or chat: judge the approach and the stated items, not whether a command ran.)
