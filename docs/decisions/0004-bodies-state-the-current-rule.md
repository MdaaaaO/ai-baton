# 0004 — A unit body states the current rule only

## Status

Accepted

## Context

A skill or agent body (`skills/<name>/SKILL.md`, `agents/<name>.md` below the frontmatter) loads into the
context of every session that invokes the unit, on every invocation. The bodies had accumulated the history
of each rule next to the rule: "owner decision, <date>" provenance lines, "since <date>" notes on when a
behaviour changed, issue numbers cited as the reason for a sentence, cost arithmetic justifying a default,
and incident paragraphs explaining which bug a step prevents. Lookup material — config-key tables, metric
definitions, per-phase exit codes, long argument lists — sat inline next to the steps that need it only
occasionally.

None of that changes what a session does. It is paid for on every invoke, it ages (a date reads as stale
long before the rule is), and it makes a body hard to scan for the step a session is actually on. Five
bodies were past the 130-line target with no reference material to show for it.

## Decision

- A unit body states the current rule: what to do, in what order, and the gate that stops it. It does not
  say when the rule arrived, who decided it, or which incident motivated it.
- The when and why live in git history (commit messages, PR bodies) and, for a choice worth not
  re-litigating, in a note under `docs/decisions/`. A body may cite such a note in one line.
- Lookup material moves to `skills/<name>/reference/*.md`, read on demand and cited by section
  (`reference/<file>.md` § <Heading>). A single sentence never gets its own reference file.
- Frontmatter is exempt: `metadata.updated` / `reviewed` are dates by design.
- `kit-verify` warns on dated history in body prose — an owner-decision line, a `YYYY-MM-DD` date, or
  "added/retired <year>" — with fenced blocks and inline code exempt (a template's example date, a fixture
  command). `--loading-table` lists every unit's body lines and bytes, so growth shows by name.

## Consequences

- Invokes are smaller: the five bodies that were restructured each lost at least 30% of their bytes, and the
  kit's total body bytes dropped accordingly (`docs/loading.md` § Layer 2).
- A reader who wants the reason for a rule follows the git blame or the decision note instead of reading it
  inline — one hop more, paid only by the reader who asks.
- Reference files may keep dated history; only bodies are checked. A rule that needs its rationale to be
  followed correctly keeps the rationale as part of the rule, stated without a date.
- The warning is a warn, not an error: a legitimate date in prose (rare) is visible in review rather than
  blocked.

## Sources

- `context-db/bin/kit_verify.py` (`dated_history_hits`, `body_sizes`, `check_body`)
- `context-db/tests/test_kit_verify_dated_history.py`, `context-db/tests/test_skill_body_size.py`
- `docs/authoring.md` § 2 Body, `docs/loading.md` § Layer 2
