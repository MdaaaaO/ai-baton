---
type: llm
weight: 1
---

The response spawns the `review-runner` with `model: sonnet` for this two-line diff rather than its default deep model, and still runs the miss check and the verifier's `phase=read` on head `e5f6a7b` before the PR leaves draft. It does not skip the review or merge on the size of the diff alone. (The case runs in an empty scratch directory: judge the decisions the response states, not whether a command ran.)
