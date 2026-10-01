---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, ticket-open]
---

File a ticket: the retry helper doubles its backoff delay even after a call succeeds, instead of
resetting it. I already know the one-line fix in the backoff function — just need it tracked.
