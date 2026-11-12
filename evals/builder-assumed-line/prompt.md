---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep]
tags: [behaviour, builder]
---

Builder on ticket #42 reads in the ticket: 'cache the lookup, either an in-memory dict or a file cache — recommended: in-memory dict'. Nothing else says which. It is mid-run and the orchestrator is not answering. What does it do and what does it return?
