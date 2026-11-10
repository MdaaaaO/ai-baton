---
name: epic-discover
description: "Opens an epic with the owner: rounds of the goal interview plus constraints, risks, verification plan, caps and grants; writes the tracking issue, context doc and task ledger. Use when starting an epic or a swarm. Not for one ticket (`ticket-open`) or child tickets."
metadata:
  version: "1"
  updated: "2026-11-09"
  reviewed: "2026-11-09"
user-invocable: true
argument-hint: "<the epic's request> [<owner/repo>]"
---

# epic-discover — the owner states the epic once

The Discover step of `$BATON/docs/swarm.md` § Lifecycle: the owner answers every question a swarm would otherwise
learn by incident, and the answers become artefacts a cold orchestrator resumes from. `ticket-open` is the ticket
grammar and `$BATON/docs/carousel.md` the question shape; this skill extends the goal interview to an epic and restates
neither. `$BATON` is the kit root, the directory that holds `skills/`. Scratch: `CTX=$(mktemp -d)`, spelled out in each
call. `E=$BATON/context-db/bin`.

## 1. Read before asking

- The request, the repo it names, and the store: `ctx_find` for an existing epic doc on the same goal (found → this is
  a re-plan delta on that epic, not a new one: say so and stop).
- `python3 $E/kit_profile.py get swarm` (`resources`, `protected_paths`, `max_workers`, `budget`; exit 1 = every
  default, `$BATON/docs/env-facts.md` names them) and `python3 $E/kit_profile.py get systems`, only to list which
  capabilities the epic touches.
- Size it: an epic's tracking issue is `opus`, main session, this skill's own rule (`$BATON/docs/delegation.md`
  § Sizing has no epic row). It is always
  interviewed; `Interview: SKIP` never applies.

## 2. The interview — rounds of one carousel each

One `AskUserQuestion` per round, at most four questions, in the shape of `docs/carousel.md` § Shape. Every
recommended option is your own read of the request, never a blank: the owner corrects a draft instead of dictating
from nothing. A free-text answer is an edit: redraft from it and re-ask that question once, in the next round. Ask the
next round only once the last is answered, because its drafts read those answers.

| Round | `header` | Question | Recommended option (your read) | Becomes |
|---|---|---|---|---|
| 1 | `Goal` · `Out of scope` · `Done when` · `First slice` | the four of `docs/carousel.md` § Goal interview, asked of the epic | as there; the Done-when preview is `- [ ] DW-<n> <criterion> · check: …` lines, the last one that every `VG-<n>` gate is closed, check `gates.py status` | the opening block's first lines and `## Done when` |
| 2 | `Constraints` | What must every child respect? | the serial resources it touches (`swarm.resources`), the capabilities and environments involved, a deadline, the protected paths (`swarm.protected_paths`), as the preview | `**Constraints**` |
| 2 | `Risks` | What could go wrong, and how is it undone? | the top risks, each with its rollback shape (revert the PR, restore from a snapshot, re-run a backfill, flag off) | `**Risks**` |
| 2 | `Verify` | How will the whole be verified? | the template, § 3; the drafted `## Verification plan` as the preview | `## Verification plan` |
| 2 | `Caps` | What may it spend, and how many workers at once? | an epic and a per-worker USD cap with your basis in the description, and `max_workers` (4 by default, fewer where a serial resource caps it). No cap is an option, never the recommended one | `**Budget**`, `swarm.py init` |
| 3 | `Grants` | Which decisions do you pre-grant, and until when? | `multiSelect`, one option per grant § 4 allows, each naming its scope and expiry; "none: ask each time" is always an option | `## Grants`, the task ledger |
| 3 | (open) | any tolerance or human gate the plan leaves for the owner, and any free-text re-ask | your read | the plan's gate text |

Nothing is created until every round is answered.

**Headless** (no `AskUserQuestion`): draft every round at once, later rounds from round 1's recommended options, and
put each draft in its block line, not in prose, with one exception: print the drafted `## Verification plan` (§ 3) as
its gate lines, a plain list (not fenced) above the Decisions after the line `Draft verification plan, not posted:`,
and let the Verify item name it: its options are the templates by name, one per option, as § 3 orders them. Above the
plan, at most three short sentences: which reads returned or were refused, that nothing was created, and, with no
session file or no write tool, that the block is the only copy; no lists, no headings. End the reply with the plain
line `Decisions` (not bold, not a heading) and one numbered line per question of every round, round 1's four each
their own line, in `docs/carousel.md` § No tool's shape, `<n>. <question> — (1) <option>, Recommended (2) <option>`:
option (1) is the recommended one, tagged `, Recommended` just before `(2)`, each option one short clause, the
question asked in full, never its header alone, and nothing follows the block. Copy it under `## Open decisions` in
your session file. A grant or a command is a draft here: name the call you would run on the answer above the block,
never inside it, and never run it. Then stop: no issue, no context doc, no ledger, no grant. The next interactive
session asks them as the rounds.

## 3. The verification template

The plan is the `## Verification plan` section of a template under `$BATON/docs/verification/`. Read each by its path
with the Read tool, never a directory listing: `code-change.md` always, since its gates and § The gate format are the
base of every plan, and `data-migration.md` for a migration epic, where a failed Read means it is absent. Copy the
gate lines as written, then adapt them; never write the gates from memory. The preview, interactive or headless, is
always the drafted `## Verification plan` itself: one `- [ ] VG-<n> … · kind: … · check: … · evidence: … · signer: …`
line per gate (§ The gate format), the template's lines copied and the added gates in the same form, never a prose
summary or a list of gate names.

- **A data migration** (the epic moves, copies, backfills or transforms stored data, or changes where data lives):
  recommend `data-migration`; offer `code-change` and `custom` beside it. Where `docs/verification/data-migration.md`
  is not in this kit, draft it: code-change's gates plus `human` gates for the mapping, the tolerances, the cutover and
  the decommission, and say so in the option.
- **Anything else**: recommend `code-change`, with its pipeline block only when the epic changes dbt models or DAGs;
  offer `custom`. Never list `data-migration` for an epic that moves no data, not even as a conditional option.
- **Custom**: the owner's own gates, drafted by you in the same format.

Each `human` gate closes later on its own decision line, `YYYY-MM-DD · VG-<n> <question>? → sign; not hold — <why>`,
the form `gates.py sign` reads: one line closes one gate, so N human gates take N lines. Discovery signs nothing;
never write a line of that form here.

## 4. Grants — what the owner pre-grants

A grant is the owner's consent recorded in the task ledger (`docs/swarm.md` § Authority matrix: scope, granted by,
expiry), so no one relays it in prose. Default expiry: the epic's deadline, else 14 days.

| Grant | Its scope names | Read by |
|---|---|---|
| A serial resource window | the resource, the hours, which lanes may hold it | the orchestrator before `ASSIGN … lease=` |
| A ticket cap raise | a ceiling in USD per ticket, once per ticket | `swarm.py assign --cap-usd`, with the grant's line as `--decision` |
| A re-plan inside an area | a split or a moved `DW-<n>` that adds no scope and no area | the orchestrator's dated delta, which cites the grant |

Never grantable, each answered when it comes up: the plan's go, a human gate's signature, an admin or bypass merge
(`docs/swarm.md` § Admin or bypass merge), a merge of a protected path, a tolerance change, an epic cap raise. An
owner who asks for one of these gets that reason in one line, and the grant is not recorded.

## 5. The artefacts, in this order

1. **The body**, drafted to `$CTX/epic.md`: `ticket-open`'s opening block, its Plan step 1 the first slice and its next
   step `epic-decompose`'s children, waves and the owner's go; then `**Constraints**`, `**Risks**` (with rollback) and
   `**Budget**` (epic and worker USD, workers at once) before the Sizing line; then `## Done when`,
   `## Verification plan` and `## Grants` (one line per grant: scope, until, its decision line). The Sketch is
   `ticket-open` step 4's. Check it: `python3 $E/done_when.py parse $CTX/epic.md`, `python3 $E/gates.py parse
   $CTX/epic.md`, the cold-reader gate (`$BATON/docs/cold-reader.md`) with its line written in, and `python3
   $E/length_check.py $CTX/epic.md --surface ticket`.
2. **The tracking issue**: `ticket-open`'s Core steps 1, 2 and 6 and its tracker adapter, public-text check included,
   with this body. Its step 1b is this skill's interview.
3. **The context doc**: `make -C $BATON/context-db new TYPE=epic DOMAIN=<domain> SLUG=<slug> KEY=<key> TITLE="<title>"`, then
   `## Tracker & links` and `## Goal` filled through the store's write tools.
4. **The task ledger**: `python3 $E/swarm.py init <key> --title "<title>" --repo <owner/repo> --session <orchestrator
   lane> --doc <doc id> --max-workers <n> --epic-usd <n> --worker-usd <n>`. `<key>` is the tracking issue's key as a
   name (its number on GitHub); the lane is this session's unless the owner named another.
5. **Grants**: per granted option, `python3 $E/swarm.py grant <key> --scope "<scope>" --by owner --expires
   <YYYY-MM-DD> --decision "<line>"`. `--expires` is 00:00Z of that date, so the epic-deadline default passes the day
   after the deadline; the 14-day default passes today plus 15 days.
6. **Decision lines** (`docs/carousel.md` § After the answer) under the doc's *Key decisions & gotchas*: the Goal when
   the owner chose another than yours, the template, the caps, and each grant, the same line `swarm.py grant` took.
7. **`## Swarm`**: `python3 $E/swarm.py render <key>` inserted with `ctx_insert` before `## Session log`.

## 6. Hand over

One carousel: `epic-decompose` now (Recommended) · a `walkthrough` of the epic first, so the owner reads the plan before
decomposition asks for a go · stop here. Offer `epic-decompose` only when this session lists it among its skills; where
it does not (until it ships), stop here is the recommended option and the report says decomposition waits
for it. Report in one line: the issue link, the doc id, the ledger key, the gates
(`VG-1`…`VG-<n>`, how many human) and the grants.
