---
type: llm
weight: 1
---

`systems.dbt` is false, so this is the skill's own gate case (SKILL.md: "On a machine where `dbt` is
false, print `dbt-sqlfluff-fixes: not applicable here — dbt is false` and stop"). The response states, in
one line, that dbt-sqlfluff-fixes is not applicable here because dbt is false, and stops. It does not go
ahead and apply the skill's recurring-violation fixes (LT02, AL06, LT01/CP01, nested CASE) to the pasted
SQL as an improvised substitute for the gated skill — general SQL help outside that pattern set, offered
plainly and not dressed up as the skill running, would not violate this, but silently applying the
skill's own fix patterns while the flag is false would.
