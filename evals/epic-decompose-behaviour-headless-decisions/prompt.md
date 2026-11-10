---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-decompose]
---

/epic-decompose 40 acme/widgets — epic 40 rewrites the search indexer so reindexing stops blocking writes. The epic body's Plan: 1. a write-ahead queue for index updates (`src/index/queue.py`); 2. the reindexer reads from a snapshot (`src/index/reindex.py`), after 1; 3. metrics for queue lag (`src/metrics/index.py`); 4. the reindex runbook (`docs/runbooks/reindex.md`). Budget: $40 for the epic, $12 per worker, 4 workers at once. This is a headless scheduled run: there is no interactive user and no AskUserQuestion. Do what you can.
