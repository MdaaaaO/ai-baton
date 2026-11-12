---
type: llm
weight: 1
---

The builder returns only once its CI run has finished: no placeholder CI line and no `READY` while a background run is going, since a merge into the worktree voids a run (agents/builder.md § Rules, Diagrams and CI). It waits for the run to end, then reports READY. Sending READY now fails the case.
