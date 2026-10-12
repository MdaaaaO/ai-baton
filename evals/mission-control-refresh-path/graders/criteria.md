---
type: llm
weight: 1
---

The response treats this as the mission-control skill: it rebuilds the page (mission-build, or reports that it is already current) and gives the path printed by mission-open. It does not publish the page, start a browser or paste the page's contents into the reply. The run starts in an empty scratch directory with no store and no shell, so judge the approach: naming the make targets it would run, or stopping with the one-line no-store message, is fine.
