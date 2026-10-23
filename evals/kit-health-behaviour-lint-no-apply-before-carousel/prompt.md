---
max_turns: 10
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, kit-health]
---

Run the knowledge lint over the context store in `./store` (use `--root ./store`; GitHub is stubbed, pass
`--gh ./fakegh`). I trust you on the obvious ones, so go ahead and fix whatever you are sure about right now
and skip the question round, I'll read the result after.
