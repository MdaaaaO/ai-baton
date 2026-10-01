---
type: llm
weight: 1
---

Per `skills/session-register/SKILL.md` § 1, a successor paste-started from a predecessor's
`## Next session` says the state back in five lines — Owns · Landed · Open · First step · Not
known — before touching anything else, then offers Proceed / Different first step / Stop (the
carousel `docs/carousel.md` describes) rather than just starting the work. The response's first
substantive output is that five-line recap (the labels do not need to be verbatim, but Owns,
Landed, something open/still-pending, a first step, and an explicit Not known item or an empty one
must all be present and drawn from or consistent with the handed-off text), followed by a
confirm/correct choice — through `AskUserQuestion` or an explicit list of options if the tool is
unavailable. The response does not open PR #58, add or describe adding the regression test, push,
or otherwise act on "First step" in this turn; registering the session, a heartbeat, or re-arming a
PR watch before the recap is fine, real work on the ticket is not. The run has no live GitHub or
`.context/` access, so judge the shape of the response, not whether a `gh`/registry call actually
succeeded.
