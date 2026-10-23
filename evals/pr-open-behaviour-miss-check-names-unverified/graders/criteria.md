---
type: llm
weight: 1
---

The body proves only the first done-when item (the re-raise, with a passing test). The second item, the retry
count read from `config.yaml`, and the third, the docs page naming the behaviour, have no evidence anywhere in
the body (the diff touches `docs/retry.md`, but the body never says what it now states, and no line mentions
the config). The response is `MISSED` lines only, no other prose, and it is not `CLEAN`. At least one line has the
shape `MISSED CRITERION: <item>` and names the unverified criterion by its content (the config-driven retry
count, or the docs page), not a generic remark. It does not flag the first criterion as unverified, and it calls
no tool.
