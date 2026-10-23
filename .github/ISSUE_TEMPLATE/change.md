---
name: Kit change
about: A behaviour change, fix or new unit in the kit — every PR starts from one of these
title: ""
labels: []
---

## What

<!-- The change in how sessions behave, in 1–3 sentences. -->

**Out of scope** — <what this change will not do, or "none beyond the goal">

## Whiteboard

<!-- Only when the change moves a session flow (a step order, a hook, a hand-off): one line — where it
     plugs in → what changes — or `Sketch: SKIP (<reason>)`. This is the issue's own gate; the drawing itself
     follows `docs/diagrams.md` (where a ticket draws only at `Sizing: opus`). -->

## Why

<!-- What went wrong or is missing — the session, skill or machine where it showed up. -->

## Capabilities

<!-- Every machine, or the `systems.*` flags it needs (a new flag, if any). Name any env-config key or migration step. -->

## Sizing

<!-- **Sizing:** `<haiku|sonnet|opus>`, <delegate|main session>. <one-line reason> — the table in
     docs/delegation.md § Sizing. `ticket-pickup` verifies this against the default branch at pickup. -->

## Done when

<!-- One line per criterion, a stable id each, and how it is checked: a `command` in backticks (runnable), a
     test, or an observation. Ids are never renumbered; a later criterion takes the next one.
     `done_when.py parse` reads this block. -->

- [ ] DW-1 <criterion> · check: <command | test | observation>

<!-- Add one area: label — area:skills, area:engine, area:sync, area:docs, area:ci. -->
