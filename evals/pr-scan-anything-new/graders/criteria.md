---
type: llm
weight: 1
---

The response treats the request as sweeping the configured repos for the user's review queue: it runs, or describes running, the queue sweep and returns a short table of PRs awaiting review (review state, unresolved threads, bot verdict) or reports exactly NO-OP when nothing is new. It does not read any single PR's diff, form findings, or post a review. The run starts in an empty scratch directory with no live GitHub access, so judge the approach, not whether the sweep found real data.
