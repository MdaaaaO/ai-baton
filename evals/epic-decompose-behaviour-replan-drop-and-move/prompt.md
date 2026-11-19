---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-decompose]
---

/epic-decompose 40 — epic 40 in acme/widgets has an approved plan: `swarm.py show 40` printed `"plan_approved": true` with rows 51 (area `src/export/runner.py` and `tests/test_runner.py`, ids DW-1 and DW-2), 52 (`src/export/retry.py` and `tests/test_retry.py`, id DW-3) and 53 (`docs/export.md`, depends on 51), all three still `ready`, no worker assigned yet. The owner just commented on the epic: "Drop #53, the docs go in the runner PR instead, and move DW-2 from 51 to 52, retries own it now." Record it.
