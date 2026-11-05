---
type: llm
weight: 1
---

Per `skills/session-register/SKILL.md` § 1 ("A successor orchestrator re-briefs"), the response sends exactly
one `ASSIGN <ticket> rebrief=1` to each worker that is live (here w-101, whose heartbeat is under 5 minutes
old) and waits for its ack. The worker with a 3-hour-old heartbeat (w-102) ended with no return: it is not
re-briefed but classified through `swarm.py resume` (drive-to-merge when a PR is open, otherwise respawn in
place or reassign). The ready row (w-103, PR #58) is not re-briefed as a live run; the open PR is re-armed or
driven to merge. Messages are not repeated per worker. The run has no live registry access, so judge the
plan, not whether a send succeeded.
