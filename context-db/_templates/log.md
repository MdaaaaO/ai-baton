---
title: {{TITLE}}
type: log
domain: {{DOMAIN}}
tags: []
status: active
updated: {{DATE}}
log_section: Log
---

# {{TITLE}}

<!-- Append-only log (on-call rotation week, self-assessment week, or an epic's
     historical tail split out of its lean current-state doc). Entries go under `## Log`, newest first, through
     `ctx_log` with `section: Log` (`--section Log` on the Bash route): the ctx-store `log` verb needs the section
     named and the kit's `log` type sets no default. Past 30 KB `ctx_maintain` rotates only the section named by
     the `log_section: <heading>` frontmatter field (`Log` above; `log_section: Day log` for a `## Day log`
     section): its oldest entries move to archive/<folder>-<slug>-log.md. A log doc without the field is never
     rotated; split it by hand. -->

## Log

- {{DATE}} —
