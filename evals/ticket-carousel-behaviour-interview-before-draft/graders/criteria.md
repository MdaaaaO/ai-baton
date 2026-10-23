---
type: llm
weight: 1
---

`ticket-carousel`'s Draft step runs the goal interview (`docs/carousel.md` § Goal interview) before a draft is
written: the four questions (goal, out of scope, how we will know it is done, the first slice verifiable on its
own), each recommending the session's own read, asked in `AskUserQuestion` carousels — or, with no tool here, as
numbered `Decisions` lines. The two candidates share a goal, so Goal and Out of scope are asked once for the group
and Done when and First slice per candidate, rather than eight separate questions. Both candidates are
sonnet-sized, so neither draft carries `Interview: SKIP (...)`. No issue is created in this turn. Flag a response
that writes both full drafts straight away from the retro text with no interview questions asked.
