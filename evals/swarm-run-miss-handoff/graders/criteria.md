---
type: llm
weight: 1
---

The response treats this as a session handoff (`session-handoff`'s job): it flushes context and writes the next-session prompt. It does not run a swarm tick or assign work. (That `swarm-run` was not invoked is `fired.md`'s job; grade the outcome here.)
