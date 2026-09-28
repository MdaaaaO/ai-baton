---
type: llm
weight: 1
---

The response treats "resume #205" as a pickup, not a fresh open: it re-reads the ticket and its Sizing line
and re-verifies the call against the current default branch (state may have moved since it was parked) before
deciding main session vs a worker. It does not treat this as creating a new ticket. (The Skill call itself is
`fired.md`'s job; grade the outcome here. The run starts in an empty scratch directory with no tracker access,
so judge the approach, not whether a lookup succeeded.)
