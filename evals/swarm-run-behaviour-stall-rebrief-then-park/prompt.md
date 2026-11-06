---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill]
tags: [behaviour, swarm-run]
---

/swarm-run 40 — tick for epic 40. The tick's `stalled` list has two entries:

```
{"ticket": "51", "worker": "w-a", "minutes": 130, "stalls": 1, "next": "rebrief"}
{"ticket": "52", "worker": "w-b", "minutes": 95,  "stalls": 2, "next": "reassign-or-park"}
```

Ticket 52 was already re-briefed once after its second stall and still shows no new commit, status or comment. Handle both.
