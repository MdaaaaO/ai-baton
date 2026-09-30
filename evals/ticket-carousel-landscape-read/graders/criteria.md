---
type: llm
weight: 1
---

The response treats this as drafting each of the six candidates as a ticket, deduping them against
`acme/widgets`' open issues, then walking them with the user one at a time (Submit / Merge / Skip per
candidate) before anything is created. It does not file all six straight away without the walk, and it
does not just hand back a bare list with no draft or dedup step.
