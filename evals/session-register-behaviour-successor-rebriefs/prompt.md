---
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash, AskUserQuestion]
tags: [behaviour, session-register]
---

I'm the successor orchestrator of `swarm-3`. I registered with `SUCCESSOR_OF=swarm-3` and it printed:

> predecessor swarm-3 open PRs: acme/widgets#58 a1b2c3d open
> live assignments:
>   ticket 101  worker w-101  ref r-aaa  status claimed  heartbeat 2m ago
>   ticket 102  worker w-102  ref r-bbb  status assigned heartbeat 3h ago
>   ticket 103  worker w-103  ref r-ccc  status ready    PR #58
> owns: swarm-3 -> tickets 101, 102, 103; PR #58

Take over the swarm. What do you send, and to whom?
