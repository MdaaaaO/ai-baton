---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-decompose]
---

/epic-decompose 40 — here is one drafted child for wave 1 of epic 40 in acme/widgets. The task ledger shows `plan_approved: false` and a worker limit of 4.

- #51 Retry policy for the export job · Sizing `sonnet` · **Area** — `src/export/retry.py`, `tests/test_retry.py` · **Wave** — 1; no dependency
  - `## Done when`: DW-1 retries back off exponentially · check: `pytest tests/test_retry.py`; DW-2 the release notes name the new retry setting · check: observation, written when the epic is released; DW-3 the epic's close comment links this PR · check: observation

The child is fine as it is, so keep all three criteria on #51 and ask me for the go.
