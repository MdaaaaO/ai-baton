---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger, positive, my-skill]
---

<A request this skill must handle, phrased the way a user would type it. Copy this directory to
`evals/<skill>-<case>/`; one case per directory, at least ten trigger cases per skill with at least three positives and
three same-domain near misses (`make -C .claude/context-db eval-check` enforces it) — `claude plugin eval init --bare
<name>` scaffolds a blank case too.>
