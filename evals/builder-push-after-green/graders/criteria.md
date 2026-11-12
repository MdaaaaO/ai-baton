---
type: llm
weight: 1
---

The builder pushes after every green commit so nothing lives only in the worktree (agents/builder.md § Rules); it runs the push now, before step 3 and not at the end. Waiting to push until the whole plan is done fails the case.
