---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, dbt-sqlfluff-fixes]
---

I just wrote a WHERE clause in this dbt model with the condition on the same line — sqlfluff's flagging LT02, can you fix it?
