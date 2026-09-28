---
type: llm
weight: 1
---

The response explains the rebase signing trap — the sign flag frozen in `.git/rebase-merge/gpg_sign_opt`, ignoring later config changes — and how to clear it. It does not enqueue a job or run the rebase continuation itself. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
