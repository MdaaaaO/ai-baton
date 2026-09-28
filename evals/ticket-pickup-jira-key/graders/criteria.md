---
type: llm
weight: 1
---

The response treats "start working on ABC-123" as a ticket pickup: it reads the ticket's Sizing line (or notes
one is missing and sizes it), verifies the call still holds, and decides main session vs a delegated worker
before starting the actual work. It does not jump into implementation with no sizing or verification step at
all. (The Skill call itself is `fired.md`'s job; grade the outcome here. The run starts in an empty scratch
directory with no tracker access, so judge the approach, not whether a lookup succeeded.)
