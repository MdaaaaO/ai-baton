---
type: llm
weight: 1
---

The response treats "continue #17" as picking the ticket back up: it reads the ticket and its Sizing line,
checks whether the call still holds against the default branch (a prior session's Plan step may already be
done), and decides main session vs a worker before resuming work. It does not restate the ticket's history as
a progress-update comment instead of picking up the work. (The Skill call itself is `fired.md`'s job; grade
the outcome here. The run starts in an empty scratch directory with no tracker access, so judge the approach,
not whether a lookup succeeded.)
