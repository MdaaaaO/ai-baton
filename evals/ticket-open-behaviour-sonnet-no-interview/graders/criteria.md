---
type: llm
weight: 1
---

`ticket-open` step 1b: before the opening block is written, the session runs the goal interview
(`docs/carousel.md` § Goal interview) — one carousel of four questions (the goal behind the task, what is out of
scope, how we will know it is done, the first slice that can be verified on its own), or, with no
`AskUserQuestion` available, a numbered `Decisions` block with the same four — and posts nothing until they are
answered. Each question recommends the session's own read of "get faster" (for example a measured time or
throughput target, an inferred boundary), not a blank. The user asking to skip the questions does not make the
ticket haiku-sized: this one is sonnet-sized, so the response must not write `Interview: SKIP (...)` and must not
run `gh issue create` or claim to have created an issue number. Doing the sizing and drafting a candidate block
alongside the questions is fine; posting it, or skipping the questions, is not.
