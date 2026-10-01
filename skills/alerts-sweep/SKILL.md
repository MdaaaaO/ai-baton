---
name: alerts-sweep
description: "Sonnet-forked sweep of the airflow-alerts Slack channel: reads all new messages (paginated), classifies each against the pattern KB, returns a state-advance trailer the main session applies, NO-OP when no message is new, a read-failed line with the error when a read failed, or `NEEDS <system>.<kind> <name>` for a missing fact. Arm with `/loop 20m /alerts-sweep`; never writes."
compatibility: "Designed for Claude Code; needs airflow, slack (systems.*)"
metadata:
  version: "14"
  updated: "2026-10-01"
  reviewed: "2026-10-01"
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
in the environment's on-call domain and are named in its env store, never here. You are read-only: on
Slack, and on `STATE`/`KB` too — this fork never writes anywhere (its `agent: triage` denies Edit/Write, and
a shell rewrite or append from Bash is the same violation by another door); every state change goes
through the trailer in Return value, which the main session applies.

> On a machine where `airflow` or `slack` is false, print `alerts-sweep: not applicable here — airflow is
> false` (or `— slack is false`, whichever tripped) and stop.

## Steps

0. `python3 $BATON/context-db/bin/kb.py get <system>.<kind> <name>` for each of `slack.channel airflow-alerts`
   → `CHANNEL`, `airflow.path alerts-state` → `STATE`, `airflow.path alerts-kb` → `KB`. Both paths are relative
   to the workspace root and must start with `.context/` — that is the whole scope this fork ever reads or
   names. If any `get` exits non-zero, or a path is absolute, contains a `..` segment or is not under
   `.context/`, return `NEEDS <system>.<kind> <name>` for the first such fact (see Return value) and stop;
   never grep a path that failed this check.
1. `grep -n 'Last swept through' "$STATE"` → `LAST_TS`.
2. `slack_read_channel(channel_id: CHANNEL, oldest: LAST_TS, response_format: concise)`. If the connector's
   answer says there is more (`next_cursor` / `has_more`), repeat with that cursor until it says there is
   none — a busy channel, or a weekend gap, returns more than one page, and the rest must never be
   silently dropped the way one un-paginated call would. Track `NEWEST_TS`, the highest ts seen across
   every page. If any page's read fails, stop and return `alerts-sweep: read failed — state not advanced`
   plus the connector's own error text (Return value) instead of continuing on a partial read, and never as
   `NO-OP` — a failed read is UNKNOWN, not "nothing to report": a swallowed error must never read as a
   negative result. Drop the message equal to `LAST_TS` itself. If
   nothing is newer across every page: go to step 5 with no changes (`NEWEST_TS` stays unset).
3. For each new message, read its thread (`slack_read_thread`) — never judge from the top level alone.
   Classify:
   - **KNOWN** — same DAG/task pair *and* same failure signature as a KB entry (grep `KB` for the
     DAG name, then the task/model name).
   - **HUMAN-RESOLVED** — a human already answered or fixed it in-thread.
   - **DIGEST/INFO** — routine digest, success notice, or bot chatter with no failure.
   - **NEW** — anything else (a DAG/task pair not in the KB, or a known pair with a different error).
4. For each KNOWN alert, keep the one recurrence-log line the main session will append to `KB`
   (`- <YYYY-MM-DD> <slack ts> <dag>/<task> — <KB entry title>`, collapsed to one line — no bare newline
   inside it) — do not append it yourself; see step 5.
5. Return (Return value) — the trailer line already carries the exact command, fully resolved; this fork
   never runs it itself.

## Return value

- If step 0 failed, return exactly `NEEDS <system>.<kind> <name>` (e.g. `NEEDS slack.channel airflow-alerts`)
  — never `NO-OP`: an unread channel is not a quiet one. This is the kit-wide **forked-worker contract**
  (`env-init` § Rules): the fork never looks for the value itself and never asks; the main session runs
  `/env-init <system>.<kind> <name>`, which follows the fact's discovery manifest (`slack_search_channels`
  for the channel; the two paths are `user` facts — asked once) and writes it back with structured
  provenance (`--from tool:slack_search_channels` / `--from user`), then re-arms the sweep.
- If a page's read failed (step 2), return `alerts-sweep: read failed — state not advanced: <error text>`
  — the connector's own error, one line, trimmed — never bare `alerts-sweep: read failed — state not
  advanced` with the error dropped, and never `NO-OP`: a failed read is UNKNOWN (seen nothing), not a
  quiet channel, so nothing in `STATE` or `KB` should move either, but the main session needs the error
  text to tell a transient read failure from an auth/permission problem worth fixing.
- If nothing was newer than `LAST_TS` (step 2 found no page with a message), return exactly the single word
  `NO-OP` and nothing else — a re-arm 20 minutes later reads the same `LAST_TS`, so there is truly nothing
  for the main session to apply.
- Otherwise at least one message was read, so `NEWEST_TS` is set (and `STATE`/`KB` are the paths step 0
  resolved) — return one of the two shapes below, always ending in a **self-executing** trailer: the fork
  writes the real, already-resolved values into it — never the literal word `STATE`/`KB`/`NEWEST_TS` and
  never an angle-bracket placeholder, because `context: fork` means only this fork ever reads the rest of
  this file; the main session sees nothing but the returned text, so the whole instruction has to travel
  inside it. `STATE`/`KB` are single-quoted in the command (`advance-state.py` refuses a path containing a
  `'` or a newline); a KNOWN recurrence line is untrusted Slack text, so it never sits on the command line
  itself — a `$`, a backtick or a `"` there would be expanded by the shell that runs it. It goes on its own
  line inside a quoted heredoc instead (`<<'DELIM'` — quoting the delimiter turns off every shell expansion
  inside it, unlike single quotes around the line itself, which still depend on the text never containing
  one):
  ```
  ADVANCE — run: python3 $BATON/skills/alerts-sweep/scripts/advance-state.py --state '<STATE>' --kb '<KB>' --ts <NEWEST_TS> --reindex
  ```
  with no `--recurrence-stdin` at all when no KNOWN alert was found — the whole trailer is that one line.
  With at least one KNOWN alert, append ` --recurrence-stdin <<'ALERTS_SWEEP_<NEWEST_TS, its `.` removed>'`,
  then one `- …` line per recurrence entry from step 4 (each already collapsed to one line, always starting
  with `- ` — the delimiter never does, so an entry can never be mistaken for it), then the bare delimiter
  line to close it:
  ```
  ADVANCE — run: python3 $BATON/skills/alerts-sweep/scripts/advance-state.py --state '<STATE>' --kb '<KB>' --ts <NEWEST_TS> --reindex --recurrence-stdin <<'ALERTS_SWEEP_<NEWEST_TS, no dot>'
  - <one recurrence line>
  ALERTS_SWEEP_<NEWEST_TS, no dot>
  ```
  (shown with placeholders here only to name the shape; what you return has real values in every slot.)
  - **No NEW alert** (only KNOWN / HUMAN-RESOLVED / DIGEST): the whole return IS the trailer above, nothing
    else — the main session runs it and moves on; it never re-reads the thread or spends a turn on it.
  - **At least one NEW alert**: ≤12 lines, one block per NEW alert (max 3; say `+N more` beyond that), then
    the same trailer:
    ```
    NEW <slack ts> <dag>/<task> — <error signature, ≤15 words>
      log: <link if in the alert> · thread: <no replies | N replies, last by <name>>
      nearest KB: <entry title | none> · next: <one suggested step for the main session>
    ```
  The main session runs the trailer verbatim, heredoc included, as one shell command — a fixed, few-second
  action (both writes plus the engine's index rebuild), not a turn spent reasoning about the channel.

Do not investigate beyond reading (no datalake queries, no Airflow logs beyond the link text, no drafts,
no Slack posts). The main session follows the on-call doc's process for NEW alerts: confirm with an
active airflow session via `.context/SESSION_INDEX.md`, then draft — never send — the thread reply.
