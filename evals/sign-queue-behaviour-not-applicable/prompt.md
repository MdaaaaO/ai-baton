---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, sign-queue]
---

This repo has no signed-commits ruleset — `systems.signed_commits` is false here, plain HTTPS remote, no
signing key required. I've staged the fix for `KEY-123` in the worktree at `/repo/.worktrees/key-123-fix`.
Queue it up for me to sign and push.
