---
type: llm
weight: 1
---

The response enqueues the job with the `--rebase` flag so the host fetches and rebases before pushing, rather than rebasing and pushing itself or just describing the rebase trap. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
