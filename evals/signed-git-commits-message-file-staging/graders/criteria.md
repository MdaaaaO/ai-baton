---
type: llm
weight: 1
---

The response covers the message-to-file convention (absolute path outside the worktree, commit-style subject) and the exact-paths staging rule, as reference before any hand-off. It does not itself enqueue the job or push. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
