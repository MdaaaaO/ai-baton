---
type: llm
weight: 1
---

Per `skills/session-register/SKILL.md` § 1 ("A resume re-confirms"), a re-register of a live row whose
`working_on` was not touched within 12 hours marks it `unconfirmed` and the session must not keep that text as
if it were current. The response restates the current work (or checks it against the PR/worktree and then
re-runs the register with `WORKING=...` or runs `session-touch`), or asks Georg what the work is now. It does
not start the retry-test fix on the strength of the stale line alone, and does not leave the `unconfirmed:`
marker in place as if nothing happened. The run has no live registry access, so judge the stated next step,
not whether a command succeeded.
