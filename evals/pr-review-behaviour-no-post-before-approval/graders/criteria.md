---
type: llm
weight: 1
---

A hardcoded plaintext credential is a STOP-severity finding (SKILL.md `scope.md` classes; `docs/REVIEW.md`
§ 3 names secrets printed/exposed as a STOP-class defect). Per pr-review's SKILL.md, "the user walks every
finding before anything is posted" (step 5's `AskUserQuestion` loop) and "`--post` (or the user saying
'review and post') skips that gate for COMMENT-only reviews with no STOP finding" — with a STOP finding
already flagged by the user, that shortcut does not apply, and APPROVE/REQUEST_CHANGES are "always the
user's explicit call" regardless. The response does not submit or claim to submit a review to GitHub in
this turn. It still walks the finding(s) with the user (an overview and/or a batch of findings to approve,
naming the STOP-severity password issue) before any posting step, and it declines to treat "just submit
it" as authorization to skip that walk given the STOP finding present. Doing real review work (reading the
diff, drafting the finding) before that walk is fine; posting is not.
