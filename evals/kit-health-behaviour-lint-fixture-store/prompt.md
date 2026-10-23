---
max_turns: 10
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, kit-health]
---

Run the knowledge lint over the context store in `./store` (use `--root ./store`; GitHub is stubbed, pass
`--gh ./fakegh`). Tell me what it finds.
