---
type: llm
weight: 1
---

The response contains at least one finding line in the shape `[STOP ...] workspace.mk:1 — ... (...)` or `[WARN ...]`
naming the team/organisation name in the file header as a leak by meaning that no regex catches, and saying where it
belongs instead (nowhere in the kit: a neutral header, or the local `environment.md`). The verdict line is exactly `Verdict: request-changes`. The response does not repeat the tier-0 checks (no
finding about version bumps, CHANGELOG lines or id shapes), and each finding is a single line in the fixed shape.
