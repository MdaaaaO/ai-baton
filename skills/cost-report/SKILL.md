---
name: cost-report
description: "The user's own Claude Code spend against the work shipped: rolling 7 days vs the prior 7, or named phases. Org mode reports billed cost with an aggregate control; private mode estimates from local transcripts. Never names anyone. Invoke as \"cost report\" or \"how efficient was last week\"."
metadata:
  version: "14"
  updated: "2026-10-01"
  reviewed: "2026-09-25"
  facts: "cost.spend_table,cost.columns,datalake.tool sql,tracker.kind,tracker.repos,tracker.query resolved_by_me,github.org"
user-invocable: true
---

# cost-report — spend vs work shipped, org or private, always with its Basis

Cost ≈ turns × prefix size, and the bill alone says nothing about whether a week was *efficient*.
This skill measures **habit** (how much context each output token drags along), **routing** (how
much of the output went to a cheap model) and **volume** (how much was produced), overlays the
**work units** the user actually shipped, and states in a **Basis** block which of these it could
compute from which source. The ratio columns (in/out, $/Mout, cheap%) are weekday-only, so they differ
slightly from a report that computes them over all days; the fixture smoke run under § Files is the reference
a reader can check.

**Config-driven.** `python3 $BATON/skills/cost-report/cost_report.py mode` prints the resolved mode and
why. Everything the script branches on lives under the optional `cost` key of the env config
(`kb.py config-set cost '{…}'`, shape in `environment-template/config.json`):

| key | meaning | default |
|---|---|---|
| `cost.spend_table` | warehouse table at grain user × model × day (billed $) | unset → private mode |
| `cost.email` | the user's identity in that table | **required** in org mode — `sql` exits 2 without it |
| `cost.filters` | SQL predicate selecting the Claude Code rows of that table (`""` = none) | **required** in org mode — the predicate is this warehouse's, not the kit's |
| `cost.columns` | the table's names for `date email model cost input output cache_read cache_write` | **required** in org mode (all eight) — proposed from `DESCRIBE TABLE` (`reference/traps.md` § Column mapping); the kit has no default schema |
| `cost.extra_repos` | `owner/repo` outside `github.org` whose PRs count (e.g. the kit repo) | `[]` |
| `cost.phases` | `[{name,start,end}]` — named windows for before/after cuts | none → rolling 7 vs prior 7 only |

**Org mode** = `systems.datalake` true **and** `cost.spend_table` set. Anything else is **private
mode**: the cost is an *estimate* at list price per model (`context-db/bin/session_stats.py`
price table), built from every transcript under `~/.claude/projects/` — `<project>/*.jsonl` plus each
session's `subagents/*.jsonl` (`context-db/bin/transcripts.py`; assistant turns, deduped per request id), optionally merged with a CSV export whose days win over the estimate.
Say which mode ran; never present an estimate as a bill.

## Default run — rolling 7 days + weekly overview

1. `python3 $BATON/skills/cost-report/cost_report.py mode` → note mode + basis.
2. **Spend rows**
   - org: `cost_report.py sql --since <join or 10 weeks back>` prints two queries (or exits 2 listing the
     `cost.*` values still unset — `cost.columns` → `reference/traps.md` § Column mapping). Run both through the warehouse's MCP
     tool — its name is the env fact `datalake.tool sql` (`kb.py get`; missing → `/env-init datalake.tool sql`,
     a roster fact: the vendor manifest names the tool, the session confirms it is callable), never from
     memory — save the results as JSON arrays of row
     objects to the scratchpad (`own.json`, `control.json`), then
     `cost_report.py ingest --rows own.json --control-rows control.json --out daily.json --control-out control.json`.
     Never print the raw result inline — a quarter of daily rows is fine, a table dump is not.
   - private: `cost_report.py collect-private --since <date> [--csv export.csv] --out daily.json`.
3. **Work units** (both optional; the Basis says which were reachable)
   - PRs: `cost_report.py work-prs --since <date> --out prs.tsv` (GitHub search
     `author:<WORKSPACE_GITHUB_LOGIN>` over `github.org` + `cost.extra_repos`; the token prefix comes
     from `kit_profile.py gh-env`). GitHub's `closed:`/`merged:` qualifiers select by UTC date while rows
     are bucketed by local day, so a unit near a window edge can land a day outside `[since, until]`.
   - Tickets, gated on `tracker.kind`: `github` → `cost_report.py work-tickets --out tickets.tsv`
     (exits 2 on a missing login or empty `tracker.repos` — never "0 tickets"). Any other kind whose
     `systems.<kind>` flag is true → run the tracker's "resolved by me since `<since>`" query (§ Tracker
     query below) through the tracker's search tool (`tracker.mcp_tools.search` — on first use record it with
     `kb.py config-set tracker.mcp_tools.search <MCP tool name suffix>`) and save `tickets.tsv` with the header
     `key created resolved type parent` (tab-separated, dates `YYYY-MM-DD`). `tracker.kind: none` or the
     flag false → skip tickets; the Basis says "tickets: not supplied" and `$/ticket` stays `–`. No
     substitute source.
4. `cost_report.py report --daily daily.json [--control control.json] [--prs prs.tsv] [--tickets tickets.tsv] --view all --out report.md --json report.json`
   — `--view rolling7|weekly|phases` narrows; `--phase NAME=START..END` (repeatable) overrides `cost.phases`.
5. Deliver **the Basis block first**, then the table(s). Terminal reply: the Basis rows that matter
   (mode, cost basis, what was skipped) + the rolling-7 line + one sentence on the decomposition. The
   full markdown goes to a `.context/reference/` doc (`make -C $BATON/context-db new TYPE=reference …`)
   when the user wants to keep it; a `.context/` doc never goes to an external surface.

### Tracker query for step 3 (tickets)

The query text is an environment fact, not kit content: `kb.py get tracker.query resolved_by_me` — if
missing, `/env-init tracker.query resolved_by_me`: the tracker's manifest (`tracker-jira` / `tracker-github`,
picked by `tracker.kind`) derives the "issues I was assigned to, resolved on or after `{since}`, oldest
first" query in the tracker's own language, verifies it against a real date and stores it with `{since}`
left as the placeholder this skill fills per run (`--from derived:tracker.kind`). A field-selection trap
you hit (some trackers drop the parent field when many fields are requested; paginate the way the tool
paginates) goes into the row's `--purpose`, never into the query text. The TSV needs exactly the
issue key, created date, resolved date, issue type and parent key. Epics are excluded from the count by
the script (they are containers, not units); `parent` feeds the "epics touched" line. Tickets and PRs
are the only work units — lines changed are never a denominator.

## Metrics — what each one is and what it is not

| metric | definition | reads as |
|---|---|---|
| **in/out** | (input + cache-write + cache-read) ÷ output tokens, weekdays only | prefix dragged per output token — the **habit** number, lower is better |
| **fixed $/Mout** | every token repriced at ONE list price (`session_stats.DEFAULT_PRICES`, the top model's) ÷ Mout | habit in $, free of routing |
| **$/Mout** | billed (or estimated) $ ÷ million output tokens | habit × routing |
| **cheap%** | Sonnet/Haiku share of weekday output tokens; `–` when any row has no model (`unknown`, CSV without a model column) | the **routing** signal — unknown is not 0 % cheap |
| **$/wday, Mout/wday** | weekday-only $ and output ÷ weekdays (ISO 1–5) | rate; every ratio above is weekday-only too, so the decomposition is an exact identity; weekend rows count only in the $ total |
| **$/PR, $/ticket** | $ ÷ PRs opened / tickets closed in the window | noisy under ~10 units — read per phase, never per week |
| **decomposition** | $/wday change = volume × habit × routing, log-additive shares | which lever moved; `avoided $/wday` = after-output at before-$/Mout minus actual |
| **control** (org only) | every other user in the table, weekdays, per user-day, aggregate | did the market move too? never names, never ranks |

## Rules baked in

- **Weekdays only for rates.** A Saturday with $9 halves a per-day average. For the control row this
  filter runs in `ingest`, in Python (`date.isoweekday()`), not in SQL — the control query carries no
  weekday predicate for a warehouse to reject.
- **Cap work data at both the first and the last spend day.** A PR opened before the first or after the
  last billed day inflates $/PR in either direction.
- **Draft PRs are not opened work.** `work-prs` excludes a hit with `isDraft` true — it cannot be merged
  without leaving draft first, so it never double-counts later.
- **Exclude the changeover day** from before/after buckets (`X=<day>..<day>` in `cost.phases`); it is
  neither. Compare a **steady baseline** (post-onboarding weeks) rather than the whole history.
- **Overlapping phases are not compared** (P0 ⊃ P0b): the decomposition takes non-overlapping pairs only.
- **A phase name is never `prior-7d`/`rolling-7d`** — those are the rolling pair `report` always builds;
  reusing one exits 2.
- **Cheap models are Sonnet/Haiku by name**; an org table's `model_family` column is not needed.
- **Per-epic billed cost does not exist.** The bill has no ticket dimension; the only per-epic view is the
  session ledger estimate (`.context/sessions/_ledger.md`), labelled as such, with its unlabelled share.
- **No naming, no ranking.** The report names no peer, no team member, no manager; the control is an
  aggregate. Self-analysis is the only purpose. This holds for the report, the doc and the chat reply.
- **Say what was skipped.** The Basis lists every factor that could not be computed (no control in private
  mode, tickets not supplied, estimate not bill). A number without its basis is not a finding.
- **Numbers are parsed strictly, never silently zeroed.** A row whose amount/date does not parse (not a
  number, not `YYYY-MM-DD`, not a `$`/thousands-separator form like `$1,234.50`) exits 2 naming the row
  and column instead of counting as 0. `--since`/`--until` are checked the same way, in every subcommand
  that takes them.
- **A rate-limited `gh search` retries, bounded.** `work-prs`/`work-tickets` back off and retry a few
  times on a 403/429/"rate limit" from `gh` (`COST_REPORT_RETRY_DELAY`, `docs/env-vars.md`); any other
  `gh` failure, or the retries running out, is a clear error naming the scope/repo and month — never a
  partial TSV.

## Traps

Org mode has no default schema (`cost.columns`/`cost.filters` come from this warehouse's own `DESCRIBE
TABLE`, set once per environment) — the column-mapping procedure, `gh search prs`'s row/rate caps, the
warehouse-dialect and MCP-truncation notes, the UTC-vs-local day-boundary rule, and the private-mode
$ ±20% caveat: `reference/traps.md`.

## Files

- `cost_report.py` — stdlib; subcommands `mode`, `sql`, `propose-columns`, `ingest`, `collect-private`, `work-prs`,
  `work-tickets`, `report` (`--help` is the list). Imports `kit_profile` and `session_stats` from `context-db/bin`.
- `fixtures/` — `daily.sample.json`, `control.sample.json`, `prs.sample.tsv`, `tickets.sample.tsv`
  (synthetic, three weeks, two models). Smoke run:
  `python3 cost_report.py report --daily fixtures/daily.sample.json --control fixtures/control.sample.json --prs fixtures/prs.sample.tsv --tickets fixtures/tickets.sample.tsv --phase before=2026-01-05..2026-01-16 --phase after=2026-01-19..2026-01-23`
