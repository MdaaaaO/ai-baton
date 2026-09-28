---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, dbt-sqlfluff-fixes]
---

This dbt model has a CASE WHEN nested three levels deep and sqlfluff keeps flagging the indentation — clean it up.
