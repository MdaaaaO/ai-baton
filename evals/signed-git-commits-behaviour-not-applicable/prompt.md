---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, signed-git-commits]
---

No signed-commits ruleset on this repo — `systems.signed_commits` is false, plain HTTPS remote, and this
machine has my normal git identity, no separate signing key needed. I've staged a small fix in the
working tree at `/repo`. Commit and push it for me.
