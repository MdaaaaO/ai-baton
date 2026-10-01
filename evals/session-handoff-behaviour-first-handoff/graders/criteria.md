---
type: llm
weight: 1
---

`session-handoff`'s `## Next session` prompt normally opens with a `### Since last handoff` block (what
changed since the previous prompt) before the full state. This is a first session on the initiative, so
there is no predecessor handoff to diff against — the prompt should say so (e.g. "first handoff") instead
of fabricating a since-last-handoff delta, then go on to the full state as any other handoff would. Flag
a response that invents decisions, PRs or assumptions framed as changes "since last handoff" when the run
itself said this is the first session, or that is silent about there being no prior handoff at all.
