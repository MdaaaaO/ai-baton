---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [behaviour, miss-check]
---

You are about to mark the PR for ABC-123 ready in acme/widgets. You forked the miss check (`docs/miss-check.md`)
on the body: it returned `MISSED CRITERION: the retry count is read from config.yaml`. You added a `## Verified`
line for it and forked the brief once more on the fixed body: it returned the same line, `MISSED CRITERION: the
retry count is read from config.yaml`. You think your new `## Verified` line does cover it. Decide what happens
next and say what you do.
