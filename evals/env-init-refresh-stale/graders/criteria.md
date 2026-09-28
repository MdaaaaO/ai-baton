---
type: llm
weight: 1
---

The response treats this as the `/env-init --refresh` path: listing the rows `kb.py stale` flags, re-running the tool behind each, rewriting only what changed and re-dating what didn't. It does not touch rows the store considers fresh or user-set. The run starts in an empty scratch directory with no live tool access, so judge the approach, not whether every fact actually refreshed.
