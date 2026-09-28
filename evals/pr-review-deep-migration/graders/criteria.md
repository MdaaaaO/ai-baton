---
type: llm
weight: 1
---

The response treats the request as reviewing someone else's already-open pull request as the user: it works through, or lists as what it will do, at least two of — fetching a snapshot of the PR, checking repo traps or conventions, a review pass forming findings with evidence, walking each finding with the user before posting, posting one review only after approval. It does not treat this as opening the user's own PR, and it does not just build a review queue or merge anything. The run starts in an empty scratch directory with no live GitHub access, so judge the approach, not whether an external call succeeded.
