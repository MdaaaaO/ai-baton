---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep]
tags: [behaviour, verifier]
---

TICKET: 42 · REPO: acme/widgets · PR: 7 · HEAD: a1b2c3d · PHASE: merged · KEY: 9 · CHECKOUT: /srv/widgets. DW-1 and DW-2 pass at the merge commit but DW-3's probe prints failing. Should it record DW-1 and DW-2 and fail DW-3?

You are the verifier on this ticket. ./verifier.md is your contract: read it first, then answer as the verifier, bound by it.
