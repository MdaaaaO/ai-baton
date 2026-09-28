---
title: .context DB — how it works
type: reference
domain: reference
tags: [meta, index]
status: reference
updated: 2026-09-24
---

# `.context/` — your workspace context, as a small document DB

This directory is the **durable knowledge base** for your work in this workspace.
It is structured like a tiny document database so Claude can look things up cheaply instead
of reading everything into context every session.

- **Each `*.md` doc is a row.** Its YAML frontmatter (`title/type/domain/tags/status/updated`)
  is the row's columns.
- **[`INDEX.md`](INDEX.md) is the materialized catalog** — generated from all frontmatter,
  grouped by domain. **Read it first** (it's cheap), then open only the leaf docs your task
  needs. Never hand-edit it: the kit's hook regenerates it after every change (`make index` by hand).
- **The `Makefile` is the CLI** (see below). Claude creates, indexes, verifies, finds, and
  archives docs through it, then "manages however it wants" within these conventions. The
  engine (`Makefile` + `bin/` + `_templates/`) lives **outside** this dir, at
  `$BATON/context-db/` (`.claude/context-db/` on a clone), so `.context/` holds only content.
  The canonical command is therefore `make -C $BATON/context-db <target>` (run from the workspace root); bare
  `make <target>` below is shorthand for it. The engine operates on this dir as its content
  root via a `CONTEXT` var that defaults to the sibling `../../.context`.

## The workflow (persist & access context)

```
# find what exists
cat .context/INDEX.md                 # the catalog
make -C $BATON/context-db find DOMAIN=<domain>  # docs in a domain
make -C $BATON/context-db find TAG=pii          # docs carrying a tag

# create durable knowledge
make -C $BATON/context-db new TYPE=epic DOMAIN=<domain> SLUG=key-123-foo TITLE="Foo epic"
#   … fill it through the ctx tools …   # ctx_str_replace · ctx_insert · ctx_log · ctx_fm · ctx_create

# the kit's hooks validate every write and refresh the catalog; by hand:
make -C $BATON/context-db index                 # refresh the catalog
make -C $BATON/context-db verify                # schema + freshness gate

# retire a doc
make -C $BATON/context-db archive SLUG=opine    # flips status: archived (stays queryable)
```

## Frontmatter schema (every doc)

```yaml
---
title:   Human title (shown in INDEX)
type:    epic | reference | repo | meeting | 1on1 | oncall | self-assessment | pr-review | log
domain:  reference | repos | meetings | 1on1 | self-assessment | pr-reviews | onboarding | kit-health | on-call | <environment domains — `domains` in .context/reference/env/config.json>
tags:    [short, kebab, tags]        # optional; used by `make find TAG=`
status:  active | closed | reference | archived
updated: 2026-09-24
---
```

`verify` enforces the required keys, the `type`/`status`/`domain` vocabularies, an ISO
`updated` date, and that `INDEX.md` is fresh.

## Taxonomy — where a doc lives

| Folder | Holds |
|---|---|
| `reference/` | Slow-changing cross-cutting truth: architecture, team, tools-access, priorities, repos-overview. |
| `repos/` | One thin card per repo — links + TLDRs, defer to each repo's own CLAUDE.md. |
| `pr-reviews/` | Cross-repo review workflow + per-repo traps. Update after every review. |
| `meetings/` | `YYYY-MM-DD-topic.md` — newest = source of truth. |
| `1on1/` | 1:1 notes, split out of meetings. |
| `self-assessment/` | Charter + per-week logs (the report format follows the environment: Lattice block, week file, …). |
| `kit-health/` | `kit-health` audit reports (`YYYY-MM-DD-<env>.md`, plus the local `HEALTH-<env>.md` stamp). |
| `onboarding.md` | The onboarding master checklist (done = onboarded). |
| `archive/` | Closed/superseded docs and the historical tails split off big epic docs. Indexed as `status: archived`. |
| *environment domains* | One folder per entry in `domains` of the env config (e.g. an epic area, a product, an on-call rotation); described in `reference/environment.md`. Add the rows here when you adopt them. |

**Rule:** one fact lives in exactly one doc. A **context doc** (the local living doc for an
initiative — created with `TYPE=epic`, one per tracker epic / tracking issue; *not* the ticket itself) is the
lean *current state*; its long history goes to `archive/<slug>-log.md`. Always-on behavioral
rules do **not** live here — they live in `.claude/WORKSPACE.md` § Rules (imported by the root `CLAUDE.md`). Memory notes
(`.context/memory/`, the harness auto-memory) hold situational recall and point here; they don't duplicate.

**Keep active docs lean — the 30KB guardrail.** `make verify` warns (non-fatal) when any
active (non-`archive/`, non-`status: archived`) doc exceeds **30KB** (~8k tokens). A context doc
is a *handoff* — a cold session re-reads it whole to re-ground, so an oversized one pulled in
after a compact is what makes autocompact **thrash** (context refills to the limit within a few
turns of every compact). When verify flags a doc, split it: keep the newest **Session log**
entries in the doc (roughly the last few days, ~6 entries max) and move the older tail to
`archive/<slug>-log.md` (newest-first, verbatim — relocate, don't rewrite). The archive tail can
grow without limit; it is exempt because it is never read by default.

## Live session registry (operational, not knowledge)

Several sessions may work the same epic at once. They coordinate through the **session
registry**: each session writes only its own `sessions/<name>.md` (what it is doing, what it
owns, a ≤12h heartbeat, and a `stats:` line — turns, context, tokens, rough list-price spend,
PRs/tickets touched — derived from its transcript on every heartbeat with zero model turns), and
[`SESSION_INDEX.md`](SESSION_INDEX.md) is generated from all of them — the one place to see who is
active and what each session costs. `sessions/_ledger.md` keeps one row per ended session (the
`_` prefix marks registry side files, skipped by the index). Ended sessions that nothing waits on —
ended more than `SESSION_ARCHIVE_DAYS` (7) days ago, or without a next-session prompt for
`SESSION_ARCHIVE_NOPROMPT_HOURS` (48, capped at `SESSION_ARCHIVE_DAYS`; a crashed session is ended without one,
and `session-register` / `session-touch` / `session-end` move its file back if it is resumed later) — are
swept to `sessions/archive/<name>.md` on every regeneration (`make session-archive`, `DRY=1` to
preview, `ARCHIVE_DAYS=<n>`, `NOARCHIVE=1` to skip), listed in the generated `sessions/archive/INDEX.md`. Managed with
`make session-register|touch|end|stats|archive` and `make session-index` (see the Makefile) and the
`session-register` / `session-handoff` skills. This is *operational* state, so `sessions/` and
`SESSION_INDEX.md` are **excluded** from `make index`/`make verify` and the doc count.

## Engine files (not content)

The engine — `Makefile`, `bin/` (index generator, verifier, scaffolder, session registry) and
`_templates/` — lives at `$BATON/context-db/` (`.claude/context-db/` on a clone), **not** here, so
`.context/` is pure per-engineer content: portable kit (`$BATON`) separate from content (`.context/`).
This dir therefore holds only docs, the two generated catalogs (`INDEX.md`, `SESSION_INDEX.md`), the
`sessions/` registry, the harness auto-memory `memory/` (symlinked from `~/.claude/projects/<slug>/memory`
by `setup.sh`) and per-user tool state under `state/` (e.g. `state/pr-review/`) — all four
operational, excluded from `make index`/`verify` (only `SESSION_INDEX.md`'s size is checked — `verify`
warns past 10 KB). Nothing executable
lives in `.context/`. The engine finds this dir via `CONTEXT` (default sibling `../../.context`,
overridable). Don't add knowledge under the engine dir.
