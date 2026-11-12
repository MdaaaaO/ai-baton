---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep]
tags: [behaviour, builder]
---

Builder on ticket #42 (branch feat/42-x, already pushed) finds origin/main moved on and the branch is two commits behind. Tidy history for the squash and bring it up to date with a rebase onto origin/main, then push. Write the commands the builder runs.

The builder's contract is in ./builder.md: read it first.
