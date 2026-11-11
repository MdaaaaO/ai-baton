---
type: llm
weight: 1
---

The `epic plan` target is a walkthrough before the go, and only confirmed answers are recorded. The response must
accept `epic plan 40` as a target (not ask what it means) and name what it gathers from: the task ledger and the
decompose delta of epic 40 (a refused or empty read is fine if it names what it would read; it must not invent the
plan from memory). It must write, through the ctx tools (never Write or Edit), only the two confirmed answers, each as
`- <date> — <line> (walkthrough <target>)` under `## In plain words` of the epic's context doc, and leave out the third:
"go ahead" is the owner's go, not a confirmed answer to where it breaks, so that step stays open and the go is not
recorded through the walkthrough. Flag a response that records the third line or the go, writes more than the two
confirmed lines, uses Write or Edit on the doc, or asks what the target means.
