---
name: my-skill
description: "<What it does, one clause>. Use when <the situations that should trigger it>. Not for <the near misses from the same domain that should not>."
metadata:
  version: "1"
  updated: "<YYYY-MM-DD>"
  reviewed: "<YYYY-MM-DD>"
user-invocable: true
---

# my-skill — <one line: what "done" looks like>

<Two or three sentences: the job, its input, its output. Rationale, history and worked examples go to
`reference.md`, not here. Copy this directory to `skills/<name>/` and rename `my-skill`. This is the floor tier: it runs on any machine. For the capability tier add
`compatibility: "Designed for Claude Code; needs <flag> (systems.*)"` at the top level and, under `metadata:`,
`requires: "<flag>"` and `facts: "<system>.<kind> <name>,<config.key>"` — docs/contributing.md § Skills.>

## 1. Detect

- <Capability tier only: `python3 $BATON/context-db/bin/kit_profile.py get systems.<flag>` — `false` →
  print `my-skill: not applicable here (systems.<flag> is false)` and stop. (`kit_profile.py get` prints the
  JSON literal `false`/`true`, never Python's `False`/`True`.)>
- <Each fact the body reads: `python3 $BATON/context-db/bin/kb.py get <system>.<kind> <name>`. Missing →
  `kb.py discover <system>.<kind> <name>` prints the tool call and the verify clause; run it, verify, write back
  with `kb.py set … --from tool:<name>`. Inside a fork return exactly `NEEDS <system>.<kind> <name>` instead.>

## 2. Do

1. <Imperative step.>
2. <Imperative step. A step only a specific machine can perform (sign, push, reach a host) becomes a handoff
   artefact the user runs — a file, a queued job — never a pasted one-liner.>

## 3. Report

- <One line per outcome. Cite config keys and fact names, never their values.>

## Related

- `<other-skill>` — <what it hands over or takes; by name, never by path>.
