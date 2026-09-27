---
type: llm
weight: 1
---

The response contains at least one finding line in the shape `[STOP ...] skills/pr-review/reference/learnings.md:<line> — ... (...)`
or `[WARN ...]` on the added example, naming the real-looking slug words behind the placeholder number (an organisation
and a concrete project/incident) as a leak by meaning, and saying the example should use the placeholder
`key-123-<slug>`. The verdict line is exactly `Verdict: request-changes`. The response does not repeat the tier-0 checks (no
finding about version bumps, CHANGELOG lines or id shapes), and each finding is a single line in the fixed shape.
