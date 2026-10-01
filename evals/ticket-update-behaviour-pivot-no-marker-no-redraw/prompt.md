---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, ticket-update]
---

Post a progress update on ABC-123. We pivoted: the Plan said a queue-based retry, we shipped a plain
exponential backoff in the same function instead — same file, same module, just a different algorithm.
The ticket's opening block is the usual Goal/Plan/Links/Sizing — no Sketch section, no sketch marker
anywhere in the body or comments. Write the Pivot comment.
