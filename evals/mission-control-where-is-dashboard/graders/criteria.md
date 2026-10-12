---
type: llm
weight: 1
---

The response treats this as the mission-control skill: it finds the built page with mission-open (building it first when mission-diff says it is behind or was never built) and answers with its path, saying it is a private local file. It does not guess a path from memory and does not compile a spend table by hand. The run starts in an empty scratch directory with no store and no shell, so judge the approach: naming the make targets it would run, or stopping with the one-line no-store message, is fine.
