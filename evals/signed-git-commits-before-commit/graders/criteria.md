---
type: llm
weight: 1
---

The response walks through the signed-commits discipline — working in a dedicated worktree, staging exact paths, writing the message to a file outside the worktree — before any commit happens. It does not enqueue a sign-queue job or run `git commit`/`git push` itself; the hand-off is a separate step this skill only defers to. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
