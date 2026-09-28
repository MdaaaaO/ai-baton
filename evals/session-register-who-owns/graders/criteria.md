---
type: llm
weight: 1
---

The response reads (or describes reading) `.context/SESSION_INDEX.md` to find which active session owns the billing epic and its worktree/PR, then decides whether to coordinate before touching it. It does not just start editing without checking session ownership, and it does not flush knowledge or produce a weekly report. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
