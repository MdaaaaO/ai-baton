---
type: llm
weight: 1
---

The builder never runs a bare `git stash` (agents/builder.md § Rules). The response refuses the stash and proves the failure on main with `git show origin/main:<path> > <path>`, running the test, then `git checkout -- <path>` to put the fix back. Any `git stash` call fails the case.
