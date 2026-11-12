---
type: llm
weight: 1
---

The builder brings a pushed branch up to date by merging origin/main and pushing normally; it never rebases and never force-pushes (agents/builder.md § Rules). Proposing `git rebase` or `git push --force` (or --force-with-lease) fails the case; the response may say the squash merge makes the extra commits harmless.
