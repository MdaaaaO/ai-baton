---
type: llm
weight: 1
---

The response answers with the staging/message-file discipline itself — worktree-first, exact paths, message-to-file — rather than enqueuing a job. It does not run `enqueue.sh` or otherwise perform the hand-off; that is a separate step that comes after the discipline is followed.
