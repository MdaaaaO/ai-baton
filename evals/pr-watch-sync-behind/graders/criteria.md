---
type: llm
weight: 1
---

The response treats the request as watching the user's own already-open PR(s) for actionable events — a bot verdict landing, someone else's review or comment, a check settling red, a head move or merge — and arms, or describes arming, a watch, only merging once the gates hold. It does not write a new PR body, request reviewers for a not-yet-open PR, or review someone else's PR. The run starts in an empty scratch directory with no live GitHub access, so judge the approach, not whether the watch actually started.
