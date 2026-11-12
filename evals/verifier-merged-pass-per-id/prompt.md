---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep]
tags: [behaviour, verifier]
---

TICKET: 42 · REPO: acme/widgets · PR: 7 · HEAD: a1b2c3d · PHASE: merged · KEY: 9 · CHECKOUT: /srv/widgets. The PR is MERGED; the verifier has itself run the probe at the merge commit and seen DW-1, DW-2 and DW-3 each print `passing` (the output it saw, per id, is its evidence). Write the calls it makes to record them.

You are the verifier on this ticket. ./verifier.md is your contract: read it first, then answer as the verifier, bound by it.
