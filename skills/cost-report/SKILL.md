---
name: cost-report
description: "The user's own Claude Code spend against the work shipped: rolling 7 days vs the prior 7, or named phases. Org mode reports billed cost (aggregate control); private mode estimates from local transcripts. Never names anyone. Invoke when asked for a \"cost report\" or \"how efficient was last week\"."
metadata:
  version: "17"
  updated: "2026-10-15"
  reviewed: "2026-10-15"
  facts: "cost.spend_table,cost.columns,datalake.tool sql,tracker.kind,tracker.repos,tracker.query resolved_by_me,github.org"
user-invocable: true
---

# cost-report — spend vs work shipped, org or private, always with its Basis

Cost ≈ turns × prefix size, and the bill alone says nothing about whether a week was *efficient*. This skill
measures **habit** (context dragged per output token), **routing** (output share on a cheap model) and **volume**,
overlays the **work units** the user shipped, and states in a **Basis** block which of these it could compute from
which source. What each metric means and does not: `reference/metrics.md`.

**Config-driven.** `python3 $BATON/skills/cost-report/cost_report.py mode` prints the resolved mode and why.
Everything the script branches on lives under the optional `cost` key of the env config (`kb.py config-set cost
'{…}'`; keys, defaults and which are required in org mode: `reference/config.md` § Config keys).

**Org mode** = `systems.datalake` true **and** `cost.spend_table` set. Anything else is **private mode**: the cost is
an *estimate* at list price per model, built from every local transcript (main sessions and subagents), optionally
merged with a CSV export whose days win over the estimate. Say which mode ran; never present an estimate as a bill.

## Default run — rolling 7 days + weekly overview

Private mode in one call: `make -C $BATON/context-db cost-report` (steps 1–5 below, `SINCE=`/`UNTIL=`/`CSV=`/`WORKDIR=` optional; org mode exits 2 and sends you here). By hand:

1. `python3 $BATON/skills/cost-report/cost_report.py mode` → note mode + basis.
2. **Spend rows**
   - org: `cost_report.py sql --since <join or 10 weeks back>` prints two queries (or exits 2 listing the
     `cost.*` values still unset — `cost.columns` → `reference/traps.md` § Column mapping). Run both through the
     warehouse's MCP tool — the env fact `datalake.tool sql` (`kb.py get`; missing → `/env-init datalake.tool
     sql`), never from memory — save the results as JSON arrays of row objects to the scratchpad (`own.json`,
     `control.json`), then
     `cost_report.py ingest --rows own.json --control-rows control.json --out daily.json --control-out control.json`.
     Never print the raw result inline.
   - private: `cost_report.py collect-private --since <date> [--csv export.csv] --out daily.json`.
3. **Work units** (both optional; the Basis says which were reachable; tickets and PRs are the only units)
   - PRs: `cost_report.py work-prs --since <date> --out prs.tsv` (GitHub search
     `author:<WORKSPACE_GITHUB_LOGIN>` over `github.org` + `cost.extra_repos`).
   - Tickets, gated on `tracker.kind`: `github` → `cost_report.py work-tickets --out tickets.tsv` (exits 2 on a
     missing login or empty `tracker.repos` — never "0 tickets"). Any other kind whose `systems.<kind>` flag is
     true → run `kb.py get tracker.query resolved_by_me` through the tracker's search tool and save
     `tickets.tsv` (`reference/config.md` § Tracker query). `tracker.kind: none` or the flag false → skip; the
     Basis says "tickets: not supplied" and `$/ticket` stays `–`. No substitute source.
4. `cost_report.py report --daily daily.json [--control control.json] [--prs prs.tsv] [--tickets tickets.tsv] --view all --out report.md --json report.json`
   — `--view rolling7|weekly|phases` narrows; `--phase NAME=START..END` (repeatable) overrides `cost.phases`.
5. Deliver **the Basis block first**, then the table(s). Terminal reply: the Basis rows that matter
   (mode, cost basis, what was skipped) + the rolling-7 line + one sentence on the decomposition. The
   full markdown goes to a `.context/reference/` doc (`make -C $BATON/context-db new TYPE=reference …`)
   when the user wants to keep it; a `.context/` doc never goes to an external surface.

## Rules and traps

Weekday-only rates, phase boundaries, draft-PR exclusion, the no-naming/no-ranking rule, strict number parsing and
the bounded `gh search` retry: `reference/rules.md`. Org mode has no default schema, `gh search` caps, warehouse
dialect, UTC-vs-local day edges and the private-mode ±20 % caveat: `reference/traps.md`.

## Files

`cost_report.py` — stdlib; subcommands `mode`, `sql`, `propose-columns`, `ingest`, `collect-private`, `work-prs`,
`work-tickets`, `report` (`--help` is the list). `fixtures/` — synthetic sample inputs and the smoke run:
`reference/metrics.md` § Fixture smoke run.
