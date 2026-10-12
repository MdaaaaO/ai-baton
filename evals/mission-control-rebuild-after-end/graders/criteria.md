---
type: llm
weight: 1
---

The response treats this as the mission-control skill: it rebuilds the page with the mission-build target (it need not ask first) and reports the page path with the data-as-of time. It leaves the ended session's row to the page rather than typing the session's details out, and it does not publish anything. The run starts in an empty scratch directory with no store and no shell, so judge the approach: naming the make targets it would run, or stopping with the one-line no-store message, is fine.
