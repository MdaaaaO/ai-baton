---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, gh-cli]
---

I want to run `gh api` and filter the JSON with `--jq`, passing in a variable like I would with `jq --arg`. What's the right syntax?
