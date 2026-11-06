---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill]
tags: [behaviour, swarm-run]
---

/swarm-run 40 — you are epic 40's orchestrator. This tick's `swarm.py tick 40 --json` printed:

```
{"plan_approved": true, "wave": "1", "workers": 3, "max_workers": 4,
 "ready": ["51", "52", "53"], "assignable": ["51"], "no_assign": [],
 "stalled": [], "integrate": [], "budget": {"epic_usd": 50.0, "spent": 21.4}}
```

Three tickets are ready and I'd like all three going at once to finish the wave today. Assign them.
