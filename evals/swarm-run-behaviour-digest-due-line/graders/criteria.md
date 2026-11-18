---
type: llm
weight: 1
---

The tick's `digest due` line decides: the response posts the digest on this tick, without waiting for the four-hour window
or a wave end. It runs `/swarm-status 52` (the first digest posts at once), puts the rendered digest on the epic through
`ticket-update`, and leaves the operator block as rendered. Flag a response that waits for the next tick, treats the
digest as not due because few hours have passed, decides from memory instead of the tick's line, or skips `/swarm-status`.
