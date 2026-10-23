---
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, ticket-carousel]
---

Turn these two retro ideas into tickets for acme/widgets, no parent issue: (1) make the importer retry on timeouts,
(2) make the importer log which attempt it is on. Both come from the same goal, a flakier-network importer that
stops paging people. Each is a sonnet-sized change to the importer. Just draft them for me, I'll walk them after.
