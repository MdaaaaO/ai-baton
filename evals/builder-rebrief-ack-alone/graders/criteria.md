---
type: llm
weight: 1
---

A line with `rebrief=1` gets an `ACK` alone: no `CLAIMED` is sent and the row does not move (agents/builder.md § Start step 3, docs/swarm.md). The builder then acts on the note in the worktree it already has. Sending a second `CLAIMED`, or adding a second worktree, fails the case.
