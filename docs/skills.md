# Skills and agents

Every skill the kit ships, grouped by what it helps with. A skill marked **needs `<flag>`** runs only where that
`systems.*` flag is true. Anywhere else it stops with one "not applicable here" line. Each skill's own `SKILL.md`
is the full contract. `docs/authoring.md` explains how to write a new one.

## Sessions and knowledge

- **session-register** — adds the session to the live registry (`.context/SESSION_INDEX.md`) and keeps its
  heartbeat fresh.
- **session-handoff** — flushes the context doc, indexes and stats, and writes the prompt the next session starts
  from.
- **session-retro** — forked self-check of the session against the kit: corrections, denials and rule slips, each
  judged a kit gap (offered as an issue) or a session slip (offered as a memory).
- **memory-prune** — audits the auto-memory (`MEMORY.md` and its notes) against the always-on prefix, skills and
  `.context/`; archives every original, asks once, then rewrites the notes and the index to their residue.
- **env-init** — fills or refreshes this machine's env fact store from the discovery manifests.
- **kit-setup** — scaffolds this workspace from inside a session (runs the kit's `setup.sh --personal`), once per machine.
- **kit-health** — audits the kit on this machine: frontmatter, env-value leaks, store coverage, wiring.
- **cost-report** — your Claude Code spend against the work shipped, week over week.
- **self-assessment** — writes the weekly self-assessment from `.context/` and the systems of record.
- **mission-control** — builds, checks and opens the private page that shows the store (sessions, spend, docs,
  priorities, queues, pointers) as one local HTML file; rebuilt locally, never published.

- **walkthrough** — leads you through a PR, repo area or epic (purpose, how it runs, where it breaks), then asks
  three questions in one carousel; a wrong answer re-opens the step, and only what you confirm is kept as
  `## In plain words` in the context doc.

## Pull requests

- **pr-open** — the checklist for opening a PR: diagrams derived from the diff, labels, commit style, reviewers,
  a watch.
- **pr-watch** — one low-noise Monitor per repo that surfaces only events you can act on.
- **pr-event-brief** — forked triage of one watch event, returning a brief with one recommended action.
- **pr-scan** — forked sweep that builds your review queue.
- **pr-review** — reviews someone else's PR with you, then posts one approved review. The body keeps the trigger,
  spawn, walk, post and KB write-back; the walk in full is `reference/walk.md`, the trivial-PR auto-approve
  contract `reference/auto-approve.md`.
- **gh-cli** — how to query GitHub with `gh` without hitting the known traps.

## Tracker

- **ticket-open** — creating a ticket: type, placement, labels, parent, a lean opening block.
- **ticket-update** — one dated delta comment per progress step.
- **ticket-close** — closing a ticket: outcome comment, close reason, linked PRs, context-doc flush.
- **ticket-carousel** — drafts and dedups a batch of candidate tickets, then walks them with you one at a
  time (Submit / Merge / Skip) before any is created.

## Docs

- **repo-docs** — writes or restructures a README and CONTRIBUTING in the house style, measured by
  `readme-check.py`.

## Capability tier — needs a system

- **sign-queue** — commits the host must sign and push: the discipline before a job and the queue that carries it. Needs `signed_commits`.
- **slack-draft** — every Slack message is a draft you send yourself. Needs `slack`.
- **alerts-sweep** — forked sweep of the Airflow alerts channel. Needs `airflow`, `slack`.
- **aws-sso-login** — AWS access through the SSO device-code flow. Needs `aws_sso`.
- **dbt-sqlfluff-fixes** — fixes the sqlfluff violations that keep coming back in dbt models. Needs `dbt`.
- **notion-page-review** — reviews a Notion page tree and posts the comments you approve. Needs `notion`.

## Agents

The forked workers that skills run under. Where they fit is shown in `docs/architecture.md`.

- **triage** — cheap read-only worker (Haiku) that summarises. It never posts.
- **review-runner** — Opus worker that runs `pr-review` steps 1–4 for one PR — the only place those steps and
  the bundle-vs-live-read contract are written.
- **auto-runner** — Sonnet worker for the auto-approve path on trivial PRs.
