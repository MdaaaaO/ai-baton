---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, dbt-sqlfluff-fixes]
---

We don't have any dbt project on this machine — `systems.dbt` is false, no `~/.dbt/profiles.yml`, nothing.
I'm just poking around and pasted a `.sql` file with a `WHERE` clause I suspect has an sqlfluff LT02
violation. Can you fix the lint issues in it for me?
