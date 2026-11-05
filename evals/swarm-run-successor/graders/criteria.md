---
type: llm
weight: 1
---

The response resumes from disk: it registers as the successor orchestrator (`SUCCESSOR_OF=` with `ROLE=orchestrator`), reads the ledger at step 1, and re-briefs every worker with an open assignment and a live run with `ASSIGN … rebrief=1`. It does not rely on the predecessor's conversation. (The Skill call itself is `fired.md`'s job; grade the outcome here. A case runs in an empty scratch directory with no ledger or tracker, so judge the approach, not whether a call succeeded.)
