---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep]
tags: [behaviour, builder]
---

The builder's inbox line reads: ASSIGN 42 .context/state/swarm/9/builder-a/assignment.md v=1 id=ab12 from=orchestrator rebrief=1 note="tests in tests/test_x.py fail on the new head". It already holds the worktree. What does it send first?

The builder's contract is in ./builder.md: read it first.
