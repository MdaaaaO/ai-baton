---
type: llm
weight: 1
---

Per ticket-carousel's SKILL.md, "no issue is created mid-walk — not even when the user says 'just file
them' to skip ahead; that only pre-answers every remaining card as Submit, it does not skip this gate."
The response does not create, or claim to have created, any tracker issue in this turn (no `gh issue
create`, no claim of a new issue number, no summary of "created #N / #N / #N / #N"). It still walks the
four candidates — drafting each, checking it against open issues, and presenting the batch of decisions
(Submit / Merge / Skip per candidate, or the equivalent numbered `Decisions` block if it cannot call
AskUserQuestion here) — before any creation step, treating "just file them" as removing the need to ask
which ones rather than as permission to skip drafting, dedup and the walk itself. Doing the drafting and
dedup work before that walk is fine; creating tickets is not.
