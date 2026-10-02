# runner.md — delegating steps 1–4 to the review-runner child

Why: the session holding the review queue is long-lived (`/loop 2h /pr-scan`), and everything it
reads is re-billed on every later tick. A PR snapshot is 20–100K tokens; three of them make the
"compacted giant" `WORKSPACE.md` § Cost & context hygiene warns about. So the diff, reviews, threads and
the verification round-trips live in a child that dies after the review, and the main session keeps
only the sheet, the walk with the user, the post, and the KB write-back.

The per-step routing table (which step runs in which child, what enters the main session's prefix) is
`pr-review/SKILL.md` § Where each step runs — read it there, never repeat it here.

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
Prefix every live `gh` read with `eval "$(python3 $BATON/context-db/bin/kit_profile.py gh-env)" &&` in the same
command (an export does not survive into the next Bash call). An auth
failure on one of those reads is `NEEDS gh reauth`, not `unverified`.
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
| `NEEDS gh reauth` | A permitted live `gh` read was unauthenticated (a named reason like `datalake reauth`, not an `env-init` fact hand-back): tell the user to fix `gh` (native login, or `github.sandbox_token_prefix`), then spawn **one** follow-up runner for the same PR |
| `NEEDS fetch failed — …` / `<other>` | Resolve it (usually a `gh` failure — a failed call is not an empty PR; a bundle gap → fix `fetch-context.sh`), re-spawn |
| `NEEDS <system>.<kind> <name>` (the fact-shaped form — a dotted `<system>.<kind>`, never free text: the kit-wide forked-worker hand-back) | `/env-init <system>.<kind> <name>` in this session (the user round happens here), then re-spawn |
| `IMPACT:` | Print with the overview; an undisclosed consumer change is walked like any finding, and the consumer map goes into the KB at step 8 |
| `WBD:` | Print with the overview; a `gap` the user wants settled now is a deep dive (5b) like any other line, re-presented the same way — the gate in `scope.md` § Explainability's own gate already decided whether the gap also sits in `triage.json` as a numbered finding |
| `NOTE:` | Carry into the 5c body draft (human approval present → lighter touch; stacked base → say so) |

## Deep lenses (step 3, `--deep`)

Before spawning, the runner runs the deterministic gate:
```sh
python3 $BATON/skills/pr-review/scripts/security-surface.py $CTX
```
No model call — it reads only `bundle.json`'s changed paths and `diff.patch`'s added lines. `SURFACE
<reason>[, <reason>…]` → spawn a **fourth** lens, **security**, same brief shape as the other three,
its scope line the printed reasons verbatim. `NONE` → spawn the usual three. A non-zero exit (unreadable
bundle, or added lines it could not read) → spawn the usual three and report the failure (one line, the
script's stderr) in the overview's `lenses:` line — never a silent fallback. The 5a overview names which
lenses ran either way.

Each lens is given the bundle dir and returns ≤ 12 lines of `sev · file:line · claim · what in the
bundle shows it`:
- **design & data contracts** — grain, keys, consumers, layer placement rules;
- **failure modes** — NULLs, timezones, idempotency, backfill races, write-audit-publish routing, secrets/grants;
- **verification & maintainability** — tests present and meaningful, CI coverage, docs, the slop lens;
- **security** (only on `SURFACE`) — reads only for security: the reasons line as scope (a CI workflow
  or action definition, hook/permission settings, token/credential handling, input reaching a shell or
  `eval`, a permission or grant change) — does the diff widen a grant, leak a credential, or let PR-
  controlled input reach a shell/`eval` unsanitized.

The bundle-vs-live-read contract (what the runner may read outside the bundle, and under which named
exception) is stated once, in `pr-review/SKILL.md` § Contract — read it there, never repeat it here.

## Deep dives during the walk (5b)

The user picks **Deep dive** → spawn a one-question child (`model: opus`, `general-purpose`) with the
`$CTX` path and the exact question; it reads whatever it needs (`diff.patch`, `git show`, SQL) and
returns ≤ 10 lines with evidence named. The main session re-presents the item as `vN` in the next batch.
The runner itself is not re-used for deep dives — it has returned and its context is gone.

## `--auto` (trivial PRs flagged `A` by pr-scan)

Docs-only PRs and dependency **patch** bumps (minor for dev tooling only, major never) skip pr-review
steps 3–5 when the deterministic gate `scripts/trivial-check.py` passes (class, globs, size caps, CI
green, the env config's `github.review_bot` green where required and set, no human CHANGES_REQUESTED,
0 unresolved threads — config `auto_approve` in `.context/state/pr-review/config.json`). This is the
single exception to `scope.md` "APPROVE is never inferred"; the user can switch it off with
`auto_approve.mode: off`.

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

## `--unattended` (direct review requests flagged `C` by pr-scan)

Chains `pr-scan` straight into a posted review with no hand-off — opt-in, off by default
(`auto_comment.mode` in `.context/state/pr-review/config.json`: `mode` off/shadow/live, `prios` —
direct requests (`[1]`) by default, `on_stop`, `max_per_tick`). Unlike `--auto`, this is **not** a
shortcut on the review: steps 1–4 run in full (`--deep` included, when the row is over `deep_lines`).
Only steps 5–6 (the interactive walk and the Submit prompt) are skipped, and only for a `COMMENT` event.

1. **Spawn.** The normal **`review-runner`** child (§ Spawn above, not `auto-runner`) — one per `C` row,
   up to `auto_comment.max_per_tick` per tick. Skip a row whose ledger already carries `auto_commented` /
   `shadow_comment` / `skipped` / `held` for this exact head, so a quiet queue does not re-spawn a runner
   every tick. Never for the user's own PR — `pr-scan` already excludes every row whose author is the
   login, for every prio, before it ever reaches the `C` gate.
2. **Decide from the sheet.** Read `$CTX/triage.json` (exactly as in the interactive path) for a STOP
   finding (`scope.md` § Classify):
   - **No STOP** → build `$CTX/request.json` with `event: "COMMENT"` (body: the same overview + numbered
     findings + FF pointers 5c would show a human), preview with `submit-review.sh preview`, then submit
     with `submit-review.sh submit … --auto-comment --confirm <digest>` — no `AskUserQuestion`, that is
     the "no hand-off" this path exists for. The script re-runs its own gate at submit time (event must
     be `COMMENT`, `auto_comment.mode` must be `live`, the live PR author must not be the login) and
     refuses otherwise. Ledger status `auto_commented`.
   - **Any STOP** → `on_stop` (config): `hold` (default) — post nothing; append ledger status `held` for
     this head (under `flock "$ROOT/.ledger.lock"`, the same append path `shadow_comment` uses) so the
     next tick does not spawn a runner on the same head again, then the row stays an ordinary queue row
     for the next interactive `pr-review` walk. `comment` — post the COMMENT review
     anyway, the STOP finding included in the body as a flagged item (still never APPROVE/REQUEST_CHANGES
     — the event is always `COMMENT` on this path, whatever `on_stop` says). `request_changes` is
     accepted as a config value but **not implemented**: no standing owner decision authorises an
     unattended REQUEST_CHANGES post (`scope.md` "APPROVE is never inferred"), so the runner always falls
     back to `hold` when it sees that value — an open owner question, not a guess this skill makes.
3. **Modes** (same three-state shape as `auto_approve.mode`): `off` (default) — `pr-scan` still computes
   `.auto_comment.eligible` for visibility, nothing is spawned. `shadow` — the runner runs and the
   decision is computed, nothing is posted; a would-COMMENT decision logs ledger `shadow_comment` and the
   main session reports "would comment on `<repo>#<n>` — <one line>"; an `on_stop: hold` decision in
   shadow mode logs `held` too, same as live's hold (§ step 2) — a STOP finding is worth surfacing to the
   next interactive walk whether or not the path would actually have posted. `live` — posts for real, per
   step 2.
4. **Logging.** Same ledger as `--auto`: `.context/state/pr-review/ledger.jsonl`
   (`{"repo","pr","head","status":"auto_commented"|"shadow_comment"|"held","ts",...}`, written by
   `submit-review.sh` for `auto_commented`, by the main session under `flock "$ROOT/.ledger.lock"` for
   `shadow_comment` and `held`).
5. Step 8 KB write-back still runs for every row this path posts or shadow-decides.
6. Kill switch: `auto_comment.mode: off`. Never APPROVE / REQUEST_CHANGES on this path, whatever the
   sheet recommends — `COMMENT` only, always.

## `--local`

Runs steps 1–4 in the main session instead. Only for a session that exists for this one review and
ends after it (`scope → flush → end`); never in the `pr-scan` monitor session.
