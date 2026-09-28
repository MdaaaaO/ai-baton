---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, alerts-sweep]
---

Check airflow-alerts for anything since the last sweep and flag anything that isn't already a known DAG failure in the KB.
