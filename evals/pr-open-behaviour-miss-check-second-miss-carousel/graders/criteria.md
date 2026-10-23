---
type: llm
weight: 1
---

A `MISSED` that survives one fix round goes to the same turn's carousel (`docs/miss-check.md` § Second-miss rule,
`docs/carousel.md`) as a question for the user, never to a third fork. The response does not fork the brief a
third time and does not mark the PR ready on its own say-so or silently drop the line because the session
believes it is covered. It puts the item into one `AskUserQuestion` carousel (or a `DECISION <question> |
<option> | <option>` line, if it cannot call the tool) naming the criterion (the config-driven retry count), and
it counts the item as open in the result line, `Miss check: fixed <n>, open 1` with `open` at least 1. Flag a
response that re-runs the check again, argues the line away without asking, or leaves the open item out of the
carousel.
