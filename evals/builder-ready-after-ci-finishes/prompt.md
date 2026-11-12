---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep]
tags: [behaviour, builder]
---

Builder on ticket #42 has pushed and started 'nice -n 19 make -C context-db ci JOBS=3' in the background; it is still running. The orchestrator is waiting. Should it send READY 42 now with the CI line to follow?

You are the builder on this ticket. ./builder.md is your contract: read it first, then answer as the builder, bound by it.
