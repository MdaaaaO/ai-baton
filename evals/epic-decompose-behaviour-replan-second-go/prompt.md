---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-decompose]
---

/epic-decompose 40 — epic 40 in acme/widgets has an approved plan: `swarm.py show 40` printed `"plan_approved": true` with rows 51, 52 (wave 1) and 53 (wave 2), and wave 1 is running. The owner just commented on the epic: "We also need a cleanup job for old export files, area `src/export/cleanup.py` and its test, after 52." Add it.
