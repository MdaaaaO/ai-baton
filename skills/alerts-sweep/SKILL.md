---
name: alerts-sweep
description: Sonnet-forked sweep of the airflow-alerts Slack channel: reads messages since the last-swept timestamp, classifies each against the pattern KB, advances the timestamp; returns exactly NO-OP when nothing needs the main session, or `NEEDS <system>.<kind> <name>` for a missing fact (`/env-init`). Arm with `/loop 20m /alerts-sweep`; never acts on an alert itself.
compatibility: "Designed for Claude Code; needs airflow, slack (systems.*)"
metadata:
  version: "8"
  updated: "2026-09-27"
  reviewed: "2026-09-25"
  requires: "airflow,slack"
  facts: "slack.channel airflow-alerts,airflow.path alerts-state,airflow.path alerts-kb"
context: fork
agent: triage
model: sonnet
effort: low
---

# alerts-sweep — one 20-minute pass over the Airflow alerts channel

Working directory: the workspace root. Three env facts (step 0): the alerts channel `CHANNEL`, the state
file `STATE` (holds the line `**Last swept through:** ts \`<ts>\``) and the pattern KB `KB`. Both files live
in the environment's on-call domain and are named in its env store, never here. You are read-only on Slack;
you may edit only `STATE` and `KB`.

## Steps

0. `python3 $BATON/context-db/bin/kb.py get <system>.<kind> <name>` for each of `slack.channel airflow-alerts`
   → `CHANNEL`, `airflow.path alerts-state` → `STATE`, `airflow.path alerts-kb` → `KB`. Both paths are relative
   to the workspace root and must start with `.context/` — that is the whole write scope of this fork. If any
   `get` exits non-zero, or a path is absolute, contains a `..` segment or is not under `.context/`, return `NEEDS <system>.<kind> <name>`
   for the first such fact (see Return value) and stop; never grep or `sed -i` a path that failed this check.
1. `grep -n 'Last swept through' "$STATE"` → `LAST_TS`.
2. `slack_read_channel(channel_id: CHANNEL, oldest: LAST_TS, response_format: concise)`.
   Drop the message equal to `LAST_TS` itself. If nothing newer: go to step 6 with no changes.
3. For each new message, read its thread (`slack_read_thread`) — never judge from the top level alone.
   Classify:
   - **KNOWN** — same DAG/task pair *and* same failure signature as a KB entry (grep `KB` for the
     DAG name, then the task/model name).
   - **HUMAN-RESOLVED** — a human already answered or fixed it in-thread.
   - **DIGEST/INFO** — routine digest, success notice, or bot chatter with no failure.
   - **NEW** — anything else (a DAG/task pair not in the KB, or a known pair with a different error).
4. For each KNOWN alert append one line under `## Recurrence log` at the end of `KB`
   (create the heading if missing): `- <YYYY-MM-DD> <slack ts> <dag>/<task> — <KB entry title>`.
5. Advance the state: `sed -i -E 's/(\*\*Last swept through:\*\* ts `)[0-9.]+/\1<newest ts>/' "$STATE"`
   and set its frontmatter `updated:` to today. Then `make -C $BATON/context-db index >/dev/null`.
6. Return.

## Return value

- If step 0 failed, return exactly `NEEDS <system>.<kind> <name>` (e.g. `NEEDS slack.channel airflow-alerts`)
  — never `NO-OP`: an unread channel is not a quiet one. This is the kit-wide **forked-worker contract**
  (`env-init` § Rules): the fork never looks for the value itself and never asks; the main session runs
  `/env-init <system>.<kind> <name>`, which follows the fact's discovery manifest (`slack_search_channels`
  for the channel; the two paths are `user` facts — asked once) and writes it back with structured
  provenance (`--from tool:slack_search_channels` / `--from user`), then re-arms the sweep.
- If there were no NEW alerts, return exactly the single word `NO-OP` and nothing else — even if you
  logged KNOWN recurrences or advanced the ts. The main session must not spend a turn on it.
- Otherwise return ≤12 lines, one block per NEW alert (max 3; say `+N more` beyond that):

```
NEW <slack ts> <dag>/<task> — <error signature, ≤15 words>
  log: <link if in the alert> · thread: <no replies | N replies, last by <name>>
  nearest KB: <entry title | none> · next: <one suggested step for the main session>
```

Do not investigate beyond reading (no datalake queries, no Airflow logs beyond the link text, no drafts,
no Slack posts). The main session follows the on-call doc's process for NEW alerts: confirm with an
active airflow session via `.context/SESSION_INDEX.md`, then draft — never send — the thread reply.
