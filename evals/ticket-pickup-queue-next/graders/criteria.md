---
type: llm
weight: 1
---

The response finds the next open ticket and then runs the pickup checklist on it: reads its Sizing line,
verifies it against the default branch, decides main session vs a worker and the model, and claims it before
starting. It does not simply start editing code with no ticket identified or no sizing/verification step. (The
Skill call itself is `fired.md`'s job; grade the outcome here. The run starts in an empty scratch directory
with no tracker access, so judge the approach, not whether a lookup succeeded.)
