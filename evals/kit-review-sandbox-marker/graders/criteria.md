---
type: llm
weight: 1
---

The response contains at least one finding line in the shape `[STOP ...] skills/kit-health/SKILL.md:<line> — ... (...)`
or `[WARN ...]` on the added lines, naming the product-specific env-file path or VM-id variable as one sandbox
product's marker (a leak by meaning and a step other machines cannot follow), and saying what to do instead (state the
capability generically, make it a conditional, or keep the path in the env store). The verdict line is exactly `Verdict: request-changes`. The response does not repeat the tier-0 checks (no
finding about version bumps, CHANGELOG lines or id shapes), and each finding is a single line in the fixed shape.
