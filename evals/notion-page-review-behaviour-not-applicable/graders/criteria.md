---
type: llm
weight: 1
---

`systems.notion` is false, so this is the skill's own gate case (SKILL.md: "On a machine where `notion`
is false, print `notion-page-review: not applicable here — notion is false` and stop"). The response
states, in one line, that notion-page-review is not applicable here because notion is false, and stops.
It does not attempt to fetch the page tree, propose comments, or improvise a substitute review (e.g.
walking the page from memory, or offering to review a pasted copy of it as if that satisfied the ask).
