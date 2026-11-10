---
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, epic-decompose]
---

/epic-decompose 40 — epic 40 in acme/widgets moves the nightly export onto the job queue. The ledger read printed `{"max_workers": 4, "budget": {"state": "ok", "epic_usd": 60.0, "spent": 0, "over": []}, "worker_usd": 15.0}` and the ledger has no rows yet. The four children, each already a full draft:

- A: queue client wrapper · `sonnet` · area `src/queue/client.py`, `tests/test_client.py` · no dependency
- B: export job as a queue task · `sonnet` · area `src/export/task.py`, `tests/test_task.py` · needs A
- C: retry and dead-letter policy · `opus` · area `src/queue/retry.py`, `tests/test_retry.py` · needs A
- D: runbook page · `haiku` · area `docs/runbooks/export.md` · no dependency

Plan the waves and caps.
