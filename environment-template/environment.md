---
title: Environment — <name>
type: reference
domain: reference
status: reference
tags: [env, environment]
updated: <YYYY-MM-DD>
---

# Environment: <name> — <one-line description>

The environment layer of the root `CLAUDE.md`, imported after `WORKSPACE.md`: only what differs on
this machine. Values live in the env store (`kit_profile.py get <key>`, `kb.py`); prose cites keys.

**Environment.** Tracker = **<Jira project X | GitHub issues | none>**; chat = **<Slack | none>**;
docs = **<Notion | none>**; datalake = **<warehouse | none>**; cloud = **<…>**; on-call **<…>**;
self-assessment → **<Lattice | week file>**; signed commits **<yes | no>**. Keep in step with `systems.*`.

## Domains (beside the core ones)

<`area-a/` (what it holds), `area-b/` (…) — the same list as `domains` in the env config.>

## Capabilities this machine turns on

<The `systems.*` flags that are true here and what each points at — e.g. `airflow` (which instance),
`dbt` (which project), `datalake` (which warehouse). Then how the shared skills behave here: where
`pr-open` asks for review, what `pr-watch` treats as the bot verdict (`github.review_bot`), whether
`sign-queue` is live, what `session-handoff` ticks.> The skills that are **not applicable** here are
whatever `python3 $BATON/context-db/bin/kb.py migrate --off` prints — never list them by hand.

## Conventions specific to this environment

- **Context docs** are one per <Jira epic | tracking issue>; the first section is *<Tracker> & links*.
- <How work is referred to (key format + link form on every surface).>
- <Chat-surface rules that exist here (e.g. Slack: threads must be read; drafts never auto-sent).>

## Rules (always on, this environment)

- <Only rules that need this environment's tracker/chat — the universal ones stay in `WORKSPACE.md`.
  Ticket mechanics (sprint field, labels, epic link) cite env-config keys, not values.>

## Labels

<Work-type set, one per ticket: `<label>` — <when>. `ticket-open` reads it; empty until agreed (then it asks once).>

## Repos in this folder

<`repo-a/` (what it is, who owns it), `repo-b/` (…). Details: `.context/repos/README.md`.>
