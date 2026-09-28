---
title: Self-assessment — charter
type: reference
domain: self-assessment
tags: [meta, self-assessment]
status: reference
updated: 2026-09-26
---

# Self-assessment — charter

This is the durable charter the `self-assessment` skill reads at its step 0. It exists so a fresh
session, with no memory of any week, can compose the same week file another session would have —
everything it needs is here or in `self_assessment.*` config (`kit_profile.py get self_assessment`),
never in a live session.

## The three purposes

1. **A weekly record of what actually happened**, cold-sourced from `.context/` and the systems of
   record (never from live sessions) — see the skill's Checklist step 3 for the sweep.
2. **A paste-ready deliverable** for whatever this environment's report surface is
   (`self_assessment.report`: `week-file` — the week file itself is the deliverable — or `lattice` —
   a four-section report block gets pasted into the external form).
3. **A running index of initiatives** the weeks touch, so a multi-week effort has one place
   (`initiatives/<slug>.md`) that accumulates across weeks instead of being re-derived from scratch
   each time.

## Fixed week-file format

One file per ISO week, `weeks/2026-Wnn.md`, in this section order (see
`$BATON/context-db/_templates/self-assessment.md` for the scaffold `make -C $BATON/context-db new
TYPE=self-assessment` produces):

- **Scope / Sources** — `self_assessment.scope` quoted verbatim, then which `self_assessment.sources`
  were swept and which were skipped.
- **Shipped** — PRs / tickets that shipped inside the window, status as of the window end.
- **Collaboration & reviews** — reviews given, collab comments, ownership rulings.
- **Impact** — what changed, numbers verified against their system of record.
- **Learnings** — a learning, a concern, a process observation.
- **Next week** — carry-overs and planned work.
- **Blockers / risks**.
- **Landed just after (Wn+1)** — cross-boundary hits, labelled, never silently merged in.
- The report block (four sections, present only where `self_assessment.report` is `lattice`).

`status: archived` once a week is reconciled; `status: active` with a "week in progress" banner for
a mid-week compose (see the skill's Checklist step 5).

## Initiatives model

A multi-week effort gets `initiatives/<slug>.md` — one file per initiative, gap-filled or refreshed
whenever a week's Shipped/Impact touches it. Short-lived, single-week work doesn't need one.

## Contribution protocol

Any session may drop context for a week in progress at `inbox/2026-Wnn--<initiative>.md` — its own
file only, never another session's drop. The `self-assessment` skill's session is the **sole
composer**: it alone writes `weeks/`, this README's index, and any external report block; check
`SESSION_INDEX.md` and agree ownership before composing a week another session is already on.
Processed drops move to `inbox/archive/`.

## Index

<!-- Add a row per composed week: | Week | Status | Notes | -->
