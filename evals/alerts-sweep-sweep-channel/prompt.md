---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, alerts-sweep]
---

Sweep the airflow-alerts Slack channel for anything new since the last pass and update the sweep state.
