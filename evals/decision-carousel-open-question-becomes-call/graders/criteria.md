---
type: llm
weight: 1
---

Whether to merge the retry-backoff PR now or hold it for one more occurrence is a choice only the
owner can make (it changes production behaviour either way, and nothing in the request or a default
settles it), so per `docs/carousel.md` it belongs in the carousel, not at the end of the prose reply.
The response's prose carries the diagnosis and findings; it does not also end with a typed-out
question like "merge now or wait?" — that choice is asked through the tool call instead, with the
options' consequences stated. (The tool call itself is `fired.md`'s job; grade the prose here.)
