---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, ticket-update]
---

Update issue #42: we're switching from polling to webhooks because the upstream API rate-limits us.
