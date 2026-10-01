---
type: llm
weight: 1
---

Picking `warning` with no ticket instruction is a decision the session took itself under standing-go
(`docs/assumptions.md`) — a call a reasonable owner might have made differently, not a default the
work required anyway. The PR body's `## One honest limit` section carries an `Assumed: <...> (revisit:
pr-open)` line naming that call. Flag any response that mentions the log-level choice only in prose
elsewhere, or omits it from the body entirely — the near miss this case guards against: an unstated
assumption with no `Assumed:` line anywhere a reviewer would see it.
