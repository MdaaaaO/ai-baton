# runner.md — delegating steps 1–4 to the review-runner child

Why: the session holding the review queue is long-lived (`/loop 2h /pr-scan`), and everything it
reads is re-billed on every later tick. A PR snapshot is 20–100K tokens; three of them make the
"compacted giant" `WORKSPACE.md` § Cost & context hygiene warns about. So the diff, reviews, threads and
the verification round-trips live in a child that dies after the review, and the main session keeps
only the sheet, the walk with the user, the post, and the KB write-back.

## Spawn (main session, after step 0 Coordinate)

```
Agent(
  subagent_type: "review-runner",            # $BATON/agents/review-runner.md — Opus, no CLAUDE.md
  description: "review-runner <repo>#<pr>",
  prompt: <template below>
)
```
Run it in the background; relay nothing until it returns. Never spawn two runners for one PR.

**Before spawning for a modelling PR** (`dbt/models/**`, BI-consumed tables, data-reshaping SQL): the
main session runs one cheap `select 1` through the datalake SQL MCP. An auth
error → ask the user to `/mcp` reauth **first**, then spawn — a runner that discovers the expired token
mid-review burns its budget marking rows `unverified` and forces a second runner. Same probe for the BI
tool's MCP when step 4b will be needed and `systems.datalake` is true: `python3
$BATON/context-db/bin/kit_profile.py get datalake.mcp_tools.probe` names the one tool this environment's
connector exposes for a cheap listing call (never a query); unset or unavailable → say so in the prompt
so the runner writes `not assessed` instead of trying.

The runner works from the **bundle** `fetch-context.sh` writes (`head/`, `base/`, `diffs/`, `bundle.json`,
`kb-traps.md`) — it should not spend turns on `gh api contents`, `git show` or CI logs. If a returned sheet
shows it did (many `gh`/`git` calls in its transcript, or `NEEDS fetch failed`), that is a bundle gap to
fix in the script, not a prompt to repeat.

## Prompt template

```
Review-runner for <owner/repo>#<pr>. Flags: <--deep | none>. Mode is decided by fetch-context.sh.
Follow your agent definition ($BATON/agents/review-runner.md): pr-review steps 1–4 only, write
$CTX/triage.json (+ traps.json, ff.json), return the REVIEW SHEET block with CTX/NEEDS/NOTE lines
and nothing else. Do not post anything anywhere and do not ask questions.
Extra context from the queue: <pr-scan row: prio, why-now, size, human reviews, threads/bot>.
<modelling PR: "Modelling PR — the datalake data pass and the 4b stakeholder-impact map are mandatory; write impact.json.">
<follow_up only: "Our previous review is on head <sha7>; build the Addressed/Outstanding/Superseded/Reversed ledger first.">
```

## What comes back, and what the main session does with it

| Returned line | Main session action |
|---|---|
| `REVIEW SHEET …` table | Print it verbatim as the 5a overview, then start the 5b loop from `$CTX/triage.json` |
| `CTX: <path>` | Everything from here on reads `$CTX/triage.json` only — never `diff.patch`, `files.json`, `reviews.json`, `threads.json` |
| `NEEDS datalake reauth (#…)` | Ask the user to `/mcp` reauth, then spawn **one** follow-up runner: "re-verify findings #… in `$CTX`, rewrite their `evidence`, return the changed rows" |
| `NEEDS fetch failed — …` / `<other>` | Resolve it (usually a `gh` failure — a failed call is not an empty PR; a bundle gap → fix `fetch-context.sh`), re-spawn |
| `NEEDS <system>.<kind> <name>` (the fact-shaped form — a dotted `<system>.<kind>`, never free text: the kit-wide forked-worker hand-back) | `/env-init <system>.<kind> <name>` in this session (the user round happens here), then re-spawn |
| `IMPACT:` | Print with the overview; an undisclosed consumer change is walked like any finding, and the consumer map goes into the KB at step 8 |
| `NOTE:` | Carry into the 5c body draft (human approval present → lighter touch; stacked base → say so) |

## Deep dives during the walk (5b)

The user picks **Deep dive** → spawn a one-question child (`model: opus`, `general-purpose`) with the
`$CTX` path and the exact question; it reads whatever it needs (`diff.patch`, `git show`, SQL) and
returns ≤ 10 lines with evidence named. The main session re-presents the item as `vN` in the next batch.
The runner itself is not re-used for deep dives — it has returned and its context is gone.

## `--auto` (trivial PRs flagged `A` by pr-scan)

Spawn the **Sonnet `auto-runner`** (`$BATON/agents/auto-runner.md`, ≤ 15 turns) — not the Opus review-runner;
one per `A` row, in parallel when several. Only rows whose `src` is `direct` or `team` (the gate already
refuses sweep rows: "not in the user's review scope").

```
Agent(subagent_type: "auto-runner", description: "auto-review <repo>#<pr>", prompt: "
MODE: auto · CLASS: <docs|patch-bump> · REPO: <owner/repo> · PR: <n> · HEAD: <sha>
Gate output: <the queue.json .auto object> — write it to $CTX/trivial.json first.
Return the AUTO:/CTX:/NEEDS lines only.")
```

Main session on return — `shadow` mode: append `{"repo","pr","head","status":"shadow_approve"|"shadow_fallback","ts","reason"}`
to the ledger (under `flock "$ROOT/.ledger.lock"`) and tell the user one line ("would approve <repo>#<n> — <body>" /
"fallback: <reason>"). A `shadow_fallback` row stays an ordinary queue row; a `shadow_approve` row drops from later sweeps
on that head. `live` mode: on `approve`, write `$CTX/request.json` `{"event":"APPROVE","body":<body>,"comments":[]}`, run
`submit-review.sh preview … --auto` then `submit … --confirm <digest> --auto` **without** an `AskUserQuestion` — the script
itself re-runs `trivial-check.py --head <sha>` and refuses unless the gate still passes on that exact head and
`auto_approve.mode == live`; it writes the `auto_approved` ledger row. Tell the user one line with the review link. On
`fallback`, the PR stays in the queue as an ordinary row. `NEEDS` lines are handled like any other runner.

## `--local`

Runs steps 1–4 in the main session instead. Only for a session that exists for this one review and
ends after it (`scope → flush → end`); never in the `pr-scan` monitor session.
