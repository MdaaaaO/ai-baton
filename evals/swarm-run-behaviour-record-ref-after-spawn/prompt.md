---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill]
tags: [behaviour, swarm-run]
---

/swarm-run 40 — you are epic 40's orchestrator, at step 4. `swarm.py assign 40 51 --kind subagent --name lane-51 --branch feat/51-x` and `swarm.py assign 40 52 --kind subagent --name lane-52 --branch feat/52-x` both printed `assigned`, `swarm_budget.py caps` was read for each, and both assignment files are written. You have just spawned the two subagent workers in one message, in the background; the spawn returned agent ids `a51f` for lane-51 and `a52e` for lane-52.

List, in order, every command you run next for these two rows before moving on to step 5.
