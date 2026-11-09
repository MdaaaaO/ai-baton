---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-discover]
---

/epic-discover rewrite the search indexer in acme/widgets so reindexing stops blocking writes. This is a headless scheduled run: there is no interactive user and no AskUserQuestion. Do what you can.
