---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, near-miss, dbt-sqlfluff-fixes]
---

One of our dbt tests is failing, not a lint check — the unique test on this model keeps failing and I don't know why.
