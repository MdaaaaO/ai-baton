---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-decompose]
---

/epic-decompose 40 acme/widgets — epic 40 rewrites the search indexer so reindexing stops blocking writes. This is a headless scheduled run: there is no interactive user and no AskUserQuestion. Do what you can.
