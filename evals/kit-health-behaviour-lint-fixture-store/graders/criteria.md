---
type: llm
weight: 1
---

The store in `./store` holds exactly one finding of each mechanical kind, and the response reports them from the
`ctx_lint.py check` output rather than from its own guess: an `ORPHAN` (`proj/loose-notes.md`), a `DEAD` link (the
epic's `runbook.md`), a `STALE` tracker claim (the epic says `acme/widgets#1` is open, the stub says CLOSED, with the
system of record named), and an `AGED` closed doc (`proj/old-spike.md`). It does NOT flag the dated Tracker bullet
about `#3` or the Session log line about `#4`: those are history, never a claim. It then presents the findings for a
decision (one carousel, or a numbered `Decisions` block when it cannot call the tool) with a stated consequence per
option, and edits no document in the store before the answers are in. FAIL: a finding is invented or missing, a
history line is reported as stale, or any file under `./store` is modified in this turn.
