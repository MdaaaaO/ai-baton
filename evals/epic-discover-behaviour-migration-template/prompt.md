---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-discover]
---

/epic-discover move the orders table in acme/widgets from the old database to the new warehouse schema: copy every row, backfill the history, switch the readers over, then drop the old table. Show me the verification question you would ask.
