---
type: llm
weight: 1
---

The response treats this as the mission-control skill: it points the user at the local mission page, building or refreshing it first when it is behind, instead of assembling the overview by hand from many files, and it says the page is private and read-only. It does not publish the page. The run starts in an empty scratch directory with no store and no shell, so judge the approach: naming the make targets it would run, or stopping with the one-line no-store message, is fine.
