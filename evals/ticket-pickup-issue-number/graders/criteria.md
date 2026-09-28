---
type: llm
weight: 1
---

The response treats this as picking up ticket #42: it reads the ticket and its Sizing line, checks whether the
call still holds against the default branch, and decides main session vs a background worker (and its model)
before doing the ticket's actual work. It does not skip straight to editing files without first reading the
ticket and its Sizing line. (The Skill call itself is `fired.md`'s job; grade the outcome here. The run starts
in an empty scratch directory with no workspace or tracker access, so judge the approach, not whether a lookup
succeeded — stopping early to name a missing tool or config value is fine.)
