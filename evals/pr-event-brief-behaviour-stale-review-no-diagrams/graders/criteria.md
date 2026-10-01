---
type: llm
weight: 1
---

A review verdict marked `(on older head <sha>)` is a human's stale review — still live information this
cycle (humans do not automatically re-review after a push), but it is never a `HEAD MOVED` line, so the
diagrams drift check never applies to it. The response's brief carries no `DIAGRAMS:` line at all — it
neither runs nor reports a diagrams/drift check for this event, and it never invents a `REDRAW` action.
It still answers the event on its own terms (for example: the CHANGES_REQUESTED still blocks the merge,
or a fresh review on the current head is needed) without claiming to have merged, posted, re-requested
or edited anything.
