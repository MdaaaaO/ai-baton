---
type: llm
weight: 1
---

The ticket carries no sketch marker, so `ticket-update`'s own redraw rule (`docs/diagrams.md` § The
sketch marker — the rule fires only "when the ticket has a sketch marker") does not apply: this Pivot
is a near miss for the redraw behaviour, not a case that triggers it. The response's Pivot comment
names the direction change in prose only. Flag any response that adds a before → after rendering (a
Mermaid block, or a WHERE/WHAT table or nested list), that runs or describes running `sketch.py stamp`,
or that otherwise treats this pivot as one that redraws a sketch.
