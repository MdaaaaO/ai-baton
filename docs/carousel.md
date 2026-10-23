# Carousel — how a session asks the user to decide

Owner decision, 2026-09-30: every open decision for the user goes in one `AskUserQuestion` carousel,
never a question at the end of prose (`WORKSPACE.md` § Rules → Communication). That rule is always on;
this is the spec it points to, read on demand — a skill with a decision point links here instead of
restating the shape (#354).

## What counts as a decision

A choice only the owner can make, that changes what happens next, and that nothing else already
settles — not the request, not the code, not a default, not the standing-go rule (`WORKSPACE.md` §
Rules → Workflow & scope). Anything else is decided and reported, not asked.

## Assumptions — a decision the session took itself

An assumption is one of the decisions above, taken by the session itself under standing-go
(`WORKSPACE.md` § Rules → Workflow & scope) rather than asked first — it still owes the owner a look.
Grammar, storage, the checkpoints that render a due line, and the bound on what qualifies:
`docs/assumptions.md`.

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

Act on every answer **in the same turn** — an approved choice is never banked for later. Then, for
every question answered, append one ledger line to the epic context doc's *Key decisions & gotchas*
section, in this grammar:

```
YYYY-MM-DD · <question> → <chosen>; not <rejected options> — <one clause why>
```

so the choice can be defended later without re-deriving it. Write it through the store's write
tools — `ctx_log` with `--section "Key decisions & gotchas"` (`ctx help log`: `[--section <heading>]`), or `ctx_insert`; Bash fallback
`ctx_adapter.py ctx …` — never a direct edit of the doc. Work with no epic context doc: the line
goes into the ticket's next delta comment instead (`ticket-update`), same grammar. A decision asked
through the headless `Decisions` block (below) is ledgered when it is answered, by the session that
receives the answer.

## Goal interview — four questions before a ticket's opening block

`ticket-open` (step 1b) and `ticket-carousel`'s Draft step ask these before the opening block is written, so the
person in charge of the spec agrees with it before a cold reader sees it (`docs/cold-reader.md` checks
legibility, not agreement). One carousel, exactly four questions, in this order, in the shape § Shape sets:

| `header` | question | options (recommended first) | the answer becomes |
|---|---|---|---|
| `Goal` | What is this for, and why? | the session's own read of the request (Recommended) · the nearest narrower or broader reading | the `**Goal**` line |
| `Out of scope` | What must this not do? | the boundary the session infers (Recommended) · nothing beyond the goal · a wider exclusion | the `**Out of scope**` line |
| `Done when` | How will we know it is done? | the criteria the session would write, each with its check, as the `preview` (Recommended) · a stricter set | the `## Done when` lines |
| `First slice` | What is the first slice that can be verified on its own? | the smallest such step (Recommended) · a different cut · the whole change in one go | Plan step 1, written `<step> — verified by <check>` |

The recommended option of each question is the session's own reading of the request, never a blank: the author
corrects it instead of dictating from nothing. A free-text answer is an edit: redraft from it and re-ask that
question once more, never write it unseen. Size the ticket first (`docs/delegation.md` § Sizing): the SKIP rule
below reads the Sizing line.

The `## Done when` lines use the grammar `ticket-open` documents (`- [ ] DW-<n> <criterion> · check: …`, ids from
`DW-1`); check the drafted block with `done_when.py parse` before posting. Nothing is posted, filed or created until
the author has answered all four; a headless run follows § No tool and stops at the `Decisions` block.

**Ledger.** A Goal answer other than the session's own read is ledgered in § After the answer's grammar, naming the
session's read as the rejected option: it is the one answer the ticket body cannot show on its own. The other three
answers are the block itself, their durable home, and need no ledger line.

**SKIP.** A `haiku`-sized ticket (the Sizing table's smallest row: a symptom that reproduces and a fix the request
already names, so there is no goal to choose) skips with one line in the block, `Interview: SKIP (<reason>)`, and
the block is written from the request. A `sonnet` or `opus` ticket is always interviewed and never carries the line.

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
skip it), `ticket-open` and `ticket-carousel` (the goal interview above), `pr-event-brief`'s recommended `ACTION` when standing go does not already cover it, and the
per-item walk loops in `pr-review`, `notion-page-review`, `kit-health` and `env-init` all route their
decision point through this shape instead of restating it.
