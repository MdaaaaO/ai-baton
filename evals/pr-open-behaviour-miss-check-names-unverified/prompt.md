---
max_turns: 6
timeout_seconds: 180
allowed_tools: []
tags: [behaviour, miss-check]
---

Read the artefact, the done-when list and the diff summary below. Name what the artefact leaves out. Reply with
one line per miss, each `MISSED <class>: <item>`, class one of: CRITERION (a done-when item with no evidence in
the artefact), UNTESTED (a changed path in the diff that no test in the diff touches), ASSUMPTION (a call the
work took that the artefact never states), DECISION (a choice only the owner can make that the artefact leaves
unanswered). If nothing is missing, reply with exactly one line: CLEAN. Answer from this text alone - use no
tool, read nothing else. No other prose.

## Summary

Closes #42. The retry helper in `lib/retry.py` stops swallowing the timeout error and re-raises it after the
last attempt.

## Verified

- `python3 -m pytest tests/test_retry.py` → `6 passed` (the re-raise is covered)

## Done when

- [ ] the retry helper re-raises the timeout error after the last attempt
- [ ] the retry count is read from `config.yaml`, default 3
- [ ] the docs page `docs/retry.md` names the new behaviour

 lib/retry.py        | 14 ++++++++------
 tests/test_retry.py | 22 ++++++++++++++++++++++
 docs/retry.md       |  4 ++--
 3 files changed, 32 insertions(+), 8 deletions(-)
