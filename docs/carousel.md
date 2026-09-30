# Carousel — how a session asks the user to decide

Owner decision, 2026-09-30: every open decision for the user goes in one `AskUserQuestion` carousel,
never a question at the end of prose (`WORKSPACE.md` § Rules → Communication). That rule is always on;
this is the spec it points to, read on demand — a skill with a decision point links here instead of
restating the shape (#354).

## What counts as a decision

A choice only the owner can make, that changes what happens next, and that nothing else already
settles — not the request, not the code, not a default, not the standing-go rule (`WORKSPACE.md` §
Rules → Workflow & scope). Anything else is decided and reported, not asked.

## Collection

Every decision still open since the user's last answer, not just the one that just came up: a session
that closed out a CI diagnosis two turns ago and now has a stale Slack draft to confirm asks both in
the same call, never two separate ones. A forked worker cannot call the tool at all — it returns one
line per open decision,

```
DECISION <question> | <option> | <option>
```

and the main session folds every worker's lines in with its own into one carousel.

## Shape

- **At most 4 questions per call**, ordered by how much work each blocks — the one whose answer
  unblocks the most work goes first.
- **2–4 options** per question.
- **Recommended option first**, its description ending `(Recommended)`.
- Every option's description states its **consequence** — what happens if it is picked — not just a
  label.
- `header` **≤ 12 characters**.
- `preview` only when the options are artefacts to compare (code, a config value, a message draft);
  never for a plain yes/no or a pick-one-of-named-things.

Findings and rationale stay in the prose that precedes the call; the carousel carries only the choices
still open, not the evidence behind them.

## After the answer

Act on every answer **in the same turn** — an approved choice is never banked for later. Write one
dated line per answer under the context doc's *Key decisions & gotchas* section, so the choice can be defended
later without re-deriving it.

## No tool — headless or scheduled runs

A run with no `AskUserQuestion` (a headless CLI run, a scheduled Routine) ends its reply with a
numbered `Decisions` block in the same shape — recommended option first — and lists the same items
under `## Open decisions` in the session file, so the next interactive session asks them at its
start (re-read that list yourself after a compaction until the kit re-prints it — #56 — so a batched
decision is never silently dropped):

```
Decisions
1. <question> — (1) <option>, Recommended (2) <option> (3) <option>
2. <question> — (1) <option>, Recommended (2) <option>
```

## Who calls this

`pr-watch` (merge past a settled red check, or wait), `slack-draft` (send a draft now, edit it, or
skip it), `pr-event-brief`'s recommended `ACTION` when standing go does not already cover it, and the
per-item walk loops in `pr-review`, `notion-page-review`, `kit-health` and `env-init` all route their
decision point through this shape instead of restating it.
