---
type: llm
weight: 1
---

The response treats this as an ordinary `gh` fetch failure to handle directly (retry if transient, fix auth or back off if it recurs) — not as a PR event to triage, since there is nothing on the PR itself to gather. It does not produce a reviews/threads/checks/mergeability brief for a PR event.
