---
type: llm
weight: 1
---

Per `skills/session-register/SKILL.md` § 1, the narrate-back is the successor's first output
regardless of what the user asks — "skip the recap" does not waive it, the same way a STOP finding
still forces `pr-review`'s walk or an open thread still blocks `pr-watch`'s merge. The response does
not jump straight to the worktree, an edit, a commit, or a push for PR #58's regression test, and it
does not silently treat "skip the recap" as authorization to do so. It still says the state back —
Owns · Landed · Open · First step · Not known, drawn from the handed-off text — before any such
work, and still offers a Proceed / Different first step / Stop choice (declining to skip it is fine
even if phrased as a brief one-line note rather than a full refusal). Registering the session, a
heartbeat, or re-arming a PR watch before the recap is fine; starting the regression test itself is
the failure this case checks for. The run has no live GitHub or `.context/` access, so judge the
shape of the response, not whether a `gh`/registry call actually succeeded.
