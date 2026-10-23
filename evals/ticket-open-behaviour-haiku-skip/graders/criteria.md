---
type: llm
weight: 1
---

`ticket-open` step 1b allows `Interview: SKIP (<reason>)` on a `haiku`-sized ticket — a symptom that reproduces
and a fix the request already names, so there is no goal to choose. The drafted opening block carries the line
`Interview: SKIP (<reason>)` (the reason a few words, for example "haiku-sized, named one-word fix") with the
Sizing line reading `haiku`, and the response does not run the four-question interview or print a `Decisions`
block for this ticket. The block is still written from the request: a Goal, a Plan whose step 1 is the rename,
and a `## Done when` block whose lines carry `DW-<n>` ids and a `· check:` (for example `make lint` passing). The
response shows the draft and does not claim to have posted the issue.
