---
name: session-handoff
description: "Flushes durable knowledge before a session ends or a ticket/PR/epic step lands: context doc, priorities, indexes, session stats (context doc, session file, cross-session ledger), and the paste-ready next-session prompt. Invoke when finishing work, before ending a session, or on \"wrap up / hand off / update context\"."
metadata:
  version: "19"
  updated: "2026-09-30"
  reviewed: "2026-09-27"
user-invocable: true
---

# session-handoff — flush before you end

A compacted transcript has already lost the nuance; the knowledge only survives if it is written
to the durable docs. Run this close-out whenever a ticket/PR/epic step lands, or before ending a
session. It implements the **§ Cost & context hygiene "Flush at every step"** and **"Scope → flush →
end"** rules in `$BATON/WORKSPACE.md` (the shared body of the root `CLAUDE.md`).

## Checklist

1. **Context doc** — the initiative's local living doc in its domain folder (the `.context/` file,
   *not* the tracker epic; e.g. `.context/<domain>/<key>-<slug>.md` or `.context/<domain>/epics/<slug>.md`) — find it in
   `.context/INDEX.md` or with `make -C $BATON/context-db find DOMAIN=<domain>`:
   Every write below goes through the ctx tools (`WORKSPACE.md` § The `.context/` DB; a direct `Write`/`Edit`
   of a context doc is denied), keyed by the doc's path without `.md`:
   - Add a dated one-liner to the **Session log**: `ctx_log` with the doc key and the text (the epic type
     appends it — oldest first — and dates it).
   - Update the relevant fixed section (*What was built* — PRs per repo; *Key decisions & gotchas*;
     *Infra/secrets locations*; *Remaining work*) with anything durable you learned or shipped
     (`ctx_str_replace` of the exact old text, or `ctx_insert` after a line). Keep
     the doc lean — long history goes to `.context/archive/<slug>-log.md`.
   - **Cap the Session log.** If `.context/archive/<slug>-log.md` does not exist yet, create it first
     (`ctx_new` TYPE=log, same DOMAIN as the context doc, SLUG=`<slug>-log`) — `maintain`'s own fallback for a
     missing archive doc fills in only `title`/`type`/`updated`, which the `log` type's `domain`/`status`
     rules would then refuse. Then run `ctx_maintain` on the doc's store (Bash fallback: `ctx_adapter.py ctx
     maintain`) — a no-op below the store's size guard (30KB); past it, it keeps the newest ~6 entries
     (`maintain.keep_log` in `ctx-store.json`) in the doc, oldest-first, and moves the rest verbatim into the
     archive doc, newest-first (the `log` type's order) — no manual move-and-reverse.
     `make -C $BATON/context-db verify` warns at the same **30KB** — an oversized doc thrashes any session
     that re-reads it after a compact, so run `maintain` (or split the doc) as soon as you see the warning.
   - Reference every ticket/PR as a clickable link (§ Rules).
2. **Priorities** — where `.context/reference/priorities.md` exists, tick the matching checkbox(es)
   (`ctx_str_replace`) and add the next step if one emerged (`ctx_insert`) (an environment without that
   file skips this step).
3. **Task-specific docs** — if you did a PR review, update `.context/pr-reviews/<repo>.md`
   and its README; if on-call, append `.context/on-call/rotations/<week>.md`; if it's Thursday and
   you're the self-assessment session, append `.context/self-assessment/weeks/<week>.md` — through the ctx
   tools as in step 1. New docs are created with `make -C $BATON/context-db new TYPE=… DOMAIN=… SLUG=…`.
4. **Memory** — only if a *situational* fact worth recalling emerged (not an always-on rule — those
   go to `WORKSPACE.md` § Rules). Write or update the note **and** add or fix its one-line entry in
   `MEMORY.md`. Prefer updating an existing note over adding a duplicate; delete notes proven wrong.
5. **Index integrity** — nothing to run: after every `.context/` write the kit's hooks validate the doc,
   regenerate `INDEX.md` and `SESSION_INDEX.md` and touch your registry row. A `ctx validate: …` system
   message after a write is a finding — fix it now. If you added a
   skill, add its trigger to `WORKSPACE.md` § Skills. If you added a memory note, confirm it has a
   `MEMORY.md` line (no orphans). Every `[[wikilink]]` should resolve.
6. **Signing** — only where the env config has `systems.signed_commits: true`: pending commits go through
   the `sign-queue` skill (enqueue; the user drains). Don't leave a session with unpushed signed work
   unmentioned. Elsewhere, commits are pushed directly per the repo's own conventions.
7. **Stop your monitors** — `TaskStop` every `pr-watch` / other `Monitor` this session armed *before*
   the registry step, so no event fires into a session that is ending (and the successor re-arms from a
   clean list, one watcher per PR across sessions). A session whose PRs all wait on host/human gates
   should *arrive* here after two idle Monitor windows rather than re-arming a third — an idle watcher
   costs a full-prefix wake-up per expiry, a parked session costs nothing (owner decision, 2026-09-22; `pr-watch`
   § Park when the gates are not yours).
8. **What the session did, then its stats.** First run `make -C $BATON/context-db session-activity` (zero
   model turns; the transcript's files edited, commits, PRs, tickets, drafts and compactions, identifiers only)
   and treat the list as a checklist: every item is reflected in the context doc above or named here as
   deliberately left out; no transcript → one line saying so, then flush from memory as before. The same
   block lands under `## What this session did` at `session-end`. Then the stats (owner decision,
   2026-09-19: "stats for geeks"): run
   `make -C $BATON/context-db session-stats` (zero model turns; derived from the transcript via
   `$CLAUDE_CODE_SESSION_ID`) and put:
   - the **one-liner** (the `stats:` value the registry row carries: turns · hours · ctx peak/avg ·
     cache-read · out · ~$ · compactions · tool calls · PRs · tickets · sign jobs · drafts) into the
     context doc's wind-down **Session log** entry;
   - the **block** (window, prompts, token split, spend basis, top tools, delegation, PR + ticket lists,
     hand-offs) is written for you under `## Session stats` in `.context/sessions/<name>.md` by
     `session-end` (step 9), together with one row in `.context/sessions/_ledger.md` — don't paste it
     twice. Add one line of *judgment* next to the numbers in the context doc (what drove the cost: fat
     prefix × watcher events, a CI-log read at full prefix, etc.) — the numbers alone don't teach the
     next session anything.
   Turns are deduped API requests; spend is a per-model list-price estimate and, since 2026-09-22, the
   TOTAL of main session + every subagent transcript (`<session-id>/subagents/*.jsonl`) — quote the total,
   the block shows the main/subagent split — and say "estimate" when you quote it. If the transcript isn't discoverable (no
   `CLAUDE_CODE_SESSION_ID`, e.g. a subagent), say so and skip; never invent the figures.
9. **Session registry + PR watches** — first refresh the `## Open PRs` list in
   `.context/sessions/<name>.md` (every open PR you own: `repo#n`, current head, what it waits on):
   your `pr-watch` `Monitor`s died with step 7, and the successor's `session-register` startup step
   re-arms exactly that list. Then mark the session ended:
   `make -C $BATON/context-db session-end NAME=<name> NEXT=<prompt file>` (step 10 writes the file; or
   `session-touch` if you're only pausing). The registry row carries **~$ est.** — the same TOTAL
   (main + subagents) quoted in step 8, list-price — and the prompt as their own columns.
   Keeps `.context/SESSION_INDEX.md` honest about who is still live, and `session-end` is what writes
   the stats block + ledger row of step 8. (Setup: the `session-register` skill.)
10. **Next-session prompt — only when there is one to write (owner decision, 2026-09-19; a closing session
    with nothing to hand over does not force one).** Decide first: does *this* session own follow-on work
    — an open PR, a ticket still in progress, or an agreed next step on its own epic? Work that belongs to
    another session, or that simply finished clean, does not count.
    When it is genuinely unclear whether this session owns the follow-on work, ask it as an
    `AskUserQuestion` carousel rather than deciding silently (`docs/carousel.md`).
    - **Nothing to hand over:** run `session-end` (or `session-touch`) with no `NEXT`, and say so plainly
      in your final message — "no follow-up from this session". If a stale prompt from earlier in the
      session is still on file and no longer true, withdraw it with `NEXT=none` rather than leaving it to
      mislead the successor. A pointer to another session's open PR/ticket goes in the context doc as a
      note (step 1), never into the prompt. If the lane itself is done — folded into another session,
      abandoned — and it is worth a one-line marker for a reader of the raw file, `NEXT` may hold a "No
      successor: …" note instead of being omitted; either way it is not a real prompt, so the Ended table
      never lists the session for it (`gen_sessions.py`'s `has_next_prompt`).
    - **Something to hand over:** draft the prompt a fresh session on this lane should be started with:
      ≤12 plain lines (no code fence inside), covering the session `NAME` to register (successor of `<this
      name>`), the epic and tickets *this session* owned, the files to read first (context doc sections,
      exports), what it owns and must NOT touch, the first task with its ticket, open follow-ups (drafts by
      `draft_id`, pending verdicts), this session's ledger lines — the *Key decisions & gotchas* lines dated
      today, or since the last handoff, quoted verbatim (`docs/carousel.md`), and the open PRs / watchers to
      re-arm (or "none"). The ledger lines count toward the ≤12-line cap: when they do not all fit, quote
      only the newest ones that do and point at the context doc's *Key decisions & gotchas* section for the
      rest — never a paraphrase, only verbatim lines or that pointer. Run `python3
      $BATON/context-db/bin/kit_profile.py scratch` once and use the **printed path** (a per-session dir
      that exists on every machine — never a bare `/tmp` path, which sessions overwrite; shell variables do
      not survive between tool calls, so the Write call takes the literal path) for `<dir>/next.md`, then
      pass the same path to the registry step: `make -C $BATON/context-db session-end NAME=<name>
      NEXT=<dir>/next.md` (`session-touch … NEXT=<dir>/next.md` when only pausing). It lands in `## Next
      session` of your session file — never copied into `SESSION_INDEX.md` itself, which shows only a
      starter that points at that file and section (a paste-ready "Register as the successor of `<name>`;
      your prompt is in `<path>` § Next session." plus a link) — the successor's `session-register` startup
      opens the file from there and reads the full text. Then **end your
      last chat message with the same prompt in a code block** so the user can paste it into the new
      session without opening the index. Internal surface: local paths are fine here.
11. **Coordination** — if another active session owns follow-on work (check `SESSION_INDEX.md`),
   leave the handoff in the context doc; message a peer only for a lock/handoff, not to dump
   context (§ Cost & context hygiene).
12. **Retro (optional, last)** — offer `/session-retro` in one line, above the step-10 code block (or last in the final message when there is none): "Run a
    retro of this session against the kit?". It forks, so the main session pays only for its ≤ 15-line result;
    a yes runs it now, before the session ends. Skip the offer for a short read-only session.

## Done when

The next session could pick up cold from `.context/` alone — no reliance on this transcript. Either its
start prompt is in the registry **and** in your final chat message (step 10), or there genuinely is no
follow-on work and your final message says so instead of inventing one.
Then end the session (don't let it sprawl past its ticket; `autoCompactWindow` is a backstop, not
a reason to keep a session alive).
