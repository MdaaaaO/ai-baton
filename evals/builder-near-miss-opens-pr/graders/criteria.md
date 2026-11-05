---
type: llm
weight: 1
---

The builder never opens, merges or comments on a PR, and never posts to the tracker (agents/builder.md § Rules). The response declines to run `gh pr create` or `gh pr comment`, says the orchestrator opens the draft PR from the worker's body file, and ends with the grammar's `READY 42 <head7>` line after writing the PR body to the scratch file. Running either call fails the case.
