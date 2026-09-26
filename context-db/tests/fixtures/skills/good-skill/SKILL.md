---
name: good-skill
description: Fixture skill for the env-free validator. Use when testing kit_verify --no-env. Not for real sessions.
compatibility: "Designed for Claude Code; needs slack (systems.*)"
metadata:
  version: "1"
  updated: "2026-09-26"
  reviewed: "2026-09-26"
  requires: "slack"
  facts: "slack.channel eng-help,tracker.kind"
user-invocable: true
---

# good-skill

Run `scripts/hello.sh` first; the pattern is in `$BATON/skills/good-skill/scripts/hello.sh` too, and
`scripts/*.sh` globs or `scripts/<name>.sh` placeholders are not checked.
