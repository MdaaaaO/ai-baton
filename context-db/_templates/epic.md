---
title: {{TITLE}}
type: epic
domain: {{DOMAIN}}
key: {{KEY}}
tags: []
status: active
updated: {{DATE}}
---

# {{TITLE}}

The **context doc** for this initiative — the local living handoff doc (created with
`TYPE=epic`, one per tracker epic / tracking issue; *not* the ticket itself). `key:` is that epic's
tracker key (`KEY=` on `make new`): `ctx resolve <key>` matches it first, the links below second. Keep it
current at the end of any session that touches the initiative (the `session-handoff` skill walks the
close-out). Long historical detail goes to `archive/{{SLUG}}-log.md`; this doc stays the lean
current state.

## Tracker & links
<!-- Epic / tracking issue + child tickets + PRs, each a clickable link. -->

## Goal
<!-- One paragraph: what this initiative delivers and why. -->

## What was built (PRs per repo)
<!-- repo → PRs, each linked. -->

## Key decisions & gotchas

## In plain words
<!-- Optional — only lines the operator confirmed in a `walkthrough`, each
     `- <date> — <line> (walkthrough <target>)`, at most 10, a superseded line replaced. Never model
     prose alone: a line nobody confirmed does not belong here. Most docs never need this section. -->

## Architecture
<!-- Optional — the living container/component map and model lineage, as a plain nested list
     (docs/diagrams.md's epic-doc rendering); most docs never need this section. -->

## Infra / secrets locations
<!-- Where things live; never paste secret values. -->

## Remaining work

## Session log
<!-- Dated one-liners, oldest first (`ctx_log` appends). -->
- {{DATE}} — created.
