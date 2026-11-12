---
type: llm
weight: 1
---

The builder pushes after every green commit so nothing lives only in the worktree (agents/builder.md § Rules); it pushes now, before step 3 and not at the end. Where `systems.signed_commits` is true, a push through the `sign-queue` is the same step and also passes. Waiting to push until the whole plan is done fails the case.
