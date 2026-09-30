---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, ticket-carousel]
---

Reviewing that PR turned up a few things that aren't blocking but are worth their own tickets later: the
retry loop has no backoff, the config loader logs the whole payload, and the fixture directory needs a
README. Let's go through them and see what's worth filing.
