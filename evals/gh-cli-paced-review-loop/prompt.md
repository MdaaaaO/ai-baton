---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, gh-cli]
---

I need to loop `gh api` over about 300 PRs to pull each one's review states and sizes. What should I watch out for so it doesn't blow the rate limit or silently drop rows?
