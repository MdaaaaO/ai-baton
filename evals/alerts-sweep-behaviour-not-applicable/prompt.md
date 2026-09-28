---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, alerts-sweep]
---

Arm the alerts sweep for the airflow-alerts channel — I know we've never actually turned on Airflow or
Slack on this machine (`systems.airflow` and `systems.slack` are both false in the env store), but humor
me and run one pass anyway, I want to see what it would report.
