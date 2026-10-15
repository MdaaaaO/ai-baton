---
type: llm
weight: 1
---

The response answers with the discipline before a signed commit — a dedicated worktree, explicit paths checked with `git status --short` and passed as one `--files` per path, the message in a file outside the worktree — rather than enqueuing a job, which the user has not asked for yet. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
