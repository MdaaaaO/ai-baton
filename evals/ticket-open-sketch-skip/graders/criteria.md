---
type: llm
weight: 1
---

The ask is a specified one-line bug fix, so the ticket's Sizing line names a small model (e.g. `sonnet`),
not `opus`. The near miss this case watches for: drawing a WHERE/WHAT sketch (a Mermaid block, or a
placement table/nested list) anyway. The response does not draw one — it writes a single
`Sketch: SKIP (<reason>)` line in the opening block instead, with the reason naming the small model
and that the fix is already specified. Flag any response that renders a diagram or a WHAT table for
this ticket, or that omits the `Sketch: SKIP` line entirely.
