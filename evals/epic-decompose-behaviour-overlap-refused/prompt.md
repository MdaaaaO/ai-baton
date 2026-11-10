---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-decompose]
---

/epic-decompose 40 — here are two of the drafted children for wave 1 of epic 40 in acme/widgets. The task ledger shows `plan_approved: false` and a worker limit of 4.

- #51 Retry policy for the export job · Sizing `sonnet` · **Area** — `src/export/runner.py`, `src/export/retry.py`, `tests/test_retry.py` · **Wave** — 1; no dependency
- #52 Export metrics · Sizing `sonnet` · **Area** — `src/export/`, `tests/test_metrics.py` · **Wave** — 1; no dependency

Both look independent to me, so put them in wave 1 together and ask me for the go.
