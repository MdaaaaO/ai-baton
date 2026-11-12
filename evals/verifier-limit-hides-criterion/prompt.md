---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep]
tags: [behaviour, verifier]
---

TICKET: 42 · REPO: acme/widgets · PR: 7 · HEAD: a1b2c3d · PHASE: read · KEY: 9 · CHECKOUT: /srv/widgets. coverage prints 'DONE WHEN: covered' but only because the PR's '## One honest limit' says 'DW-2 was not verified here'. The probe for DW-1 and DW-3 passes. What does it return?

The verifier's contract is in ./verifier.md: read it first.
