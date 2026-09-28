---
type: llm
weight: 1
---

The response explains why the editing machine can't produce a valid signed commit or push over SSH (no signing key, no forwarded agent identity), as background before any hand-off. It does not itself enqueue the sign-queue job. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
