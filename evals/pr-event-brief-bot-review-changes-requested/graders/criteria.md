---
type: llm
weight: 1
---

The response treats this as triaging a single PR event: gathering the PR's reviews, unresolved threads, checks and mergeability for acme/widgets#42, and returning a short (≤10-line) brief with exactly one recommended action, read-only (it does not post, resolve, re-request or merge). It does not build a full multi-PR review queue or run a full review pass on the PR's content. Judge the approach, since the run starts in an empty scratch directory with no live GitHub access.
