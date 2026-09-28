---
type: llm
weight: 1
---

The response enqueues the change as a sign-queue job for the user to drain on the host, rather than attempting `git commit`/`git push` on this machine or just explaining why signing fails here. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
