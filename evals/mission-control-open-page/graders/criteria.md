---
type: llm
weight: 1
---

The response treats this as the mission-control skill: it checks whether the page is current (mission-diff), builds it with mission-build when it is behind, gets the page path from mission-open and reports one line with that path and the data-as-of time. It does not start a browser, does not publish the page anywhere and does not answer with a prose summary of sessions or spend instead of the page. The run starts in an empty scratch directory with no store and no shell, so judge the approach: naming the make targets it would run, or stopping with the one-line no-store message, is fine.
