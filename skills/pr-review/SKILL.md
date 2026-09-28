---
name: pr-review
description: "Reviews another's PR as the user: snapshot, repo trap KB, an Opus review pass, verified claims, walks findings (Post, Deep dive, Body only, Skip), posts one review after approval, replies in threads, writes back learnings; a trivial PR (pr-scan `A`) auto-approves via Sonnet. For PRs pr-scan surfaces or the user names; never the user's own."
metadata:
  version: "25"
  updated: "2026-09-28"
  reviewed: "2026-09-27"
  facts: "systems.jira,systems.datalake,systems.slack,tracker.mcp_tools.search,datalake.mcp_tools.probe"
argument-hint: "<owner/repo> <pr> [--deep] [--post] [--local]"
---

# pr-review — review a PR, post it as the user, grow the repo KB

The user is the reviewer of record: everything posted carries their name. The model drafts, verifies
and proposes; **the user walks every finding before anything is posted** — the interactive loop in step 5,
same Post / Deep dive / Body only / Skip rhythm as `notion-page-review` (`.context/pr-reviews/README.md` step 5).
`--post` (or the user saying "review and post") skips that gate for COMMENT-only reviews with no STOP
finding; APPROVE / REQUEST_CHANGES are always the user's explicit call.

Scripts live in `scripts/`; references in `reference/` (`scope.md` — finding classes, STOP/WARN/NIT,
proportionality, event policy, follow-up ledger; `review-writing.md` — how posted text reads;
`slop.md` — AI-residue lens; `runner.md` — delegating steps 1–4 to the `review-runner` child). State: `.context/state/pr-review/` (config, ledger, `.submitted/`; override with `PR_REVIEW_HOME`). Knowledge:
`.context/pr-reviews/<repo>.md` (+ `README.md` standards). The scripts apply `github.sandbox_token_prefix`.
**Environment gates:** env-specific enrichment runs only where the env config turns it on
(`python3 $BATON/context-db/bin/kit_profile.py get systems.<x>`); a false flag skips that step with one
`<System> enrichment: n/a in this environment` line in the 5a overview — never an improvised substitute.

## Where each step runs (cost model, 2026-09-19)

The session that owns the review queue is long-lived (`/loop 2h /pr-scan`), so nothing bulky may
enter its prefix — everything read there is re-billed on every later tick. Therefore:

| Steps | Runs in | What enters the main prefix |
|---|---|---|
| 1–4 snapshot · KB · review pass · verification | **`review-runner` child** — `Agent(subagent_type: "review-runner")`, Opus `effort: medium`, ≤ 60 turns, no CLAUDE.md (`$BATON/agents/review-runner.md`; prompt + return contract in `reference/runner.md`). It works from the **bundle** `fetch-context.sh` writes (`head/`, `base/`, `diffs/`, `bundle.json`, `kb-traps.md`) plus the few named exceptions in § Contract | its return only: the 5a overview + `CTX:` / `NEEDS` / `NOTE:` lines (≤ 3K tokens) |
| `--auto` trivial-PR pass | **`auto-runner` child** — Sonnet, ≤ 15 turns (`$BATON/agents/auto-runner.md`) | `AUTO:` / `CTX:` / `NEEDS` lines |
| 5 walk · 6 post · 7 replies | **main session** — AskUserQuestion needs the user; the post carries their login | `$CTX/triage.json`, `request.json` / `replies.json`, the script previews |
| 5b deep dives | one-question `opus` `Agent` spawned from the main session | its ≤ 10-line answer |
| 8 KB write-back | main session, sourced from `triage.json` `evidence` + the walk decisions | the KB's `## Known traps` slice |

The main session never opens `$CTX/diff.patch`, `files.json`, `reviews.json` or `threads.json`
itself; if a walk question needs them, that is a deep dive (a child reads them). `--deep` spawns its
three lenses **inside** the runner (it has `Agent`), so the main session sees one return either way.
`--local` runs steps 1–4 in-session — only for a session that exists for this one review and ends
after it, never in the `pr-scan` monitor session.

### Contract: bundle vs. live reads

The runner reads only the bundle — never `git show`, never CI-log polling, no `gh api` call the bundle
already answers. Four reads are live because the bundle cannot hold them, each capped to what its step names:
a repo's own `CLAUDE.md`/`AGENTS.md` review rules when the PR did not touch that file (step 2.3, else it's
the bundle's own `head/<path>`); a base blob for a path the PR did not touch (step 4); one named file in an
external repo when a convention claim needs settling (`agents/review-runner.md` step 4); and a repo-wide
`ref()`/`exposures` search at the head ref for stakeholder impact (step 4b).

## 0. Coordinate (repos other sessions also work) *(main session)*

`grep -n "<repo>" .context/SESSION_INDEX.md` — if another *active* session lists this PR, agree
ownership with one `SendMessage` before posting anything. Reviewing is read-only until step 6, so
reading in parallel is fine; two reviews on one PR are not.

## 1. Snapshot *(runner)*

```sh
bash $BATON/skills/pr-review/scripts/fetch-context.sh <owner/repo> <pr>
```
Prints the context dir and a five-line summary; `manifest.json` has the rest. `mode`:
- `full` — no prior review by us: review the whole diff.
- `follow_up` — we reviewed an older head: build the finding ledger (Addressed / Outstanding /
  Superseded / Reversed, `scope.md`) from `reviews.json` + `threads.json`, then review only the
  delta (`git`-less: compare `files.json` patches with our previous inline comments' `diff_hunk`).
- `replies_only` — same head as our review; the author replied in our threads. Go to step 7.
If `threads_complete` is false, GraphQL thread state was unavailable: review, but do not reply or resolve.
Check `humans`, `bot_assessment`, `checks.not_green` — a PR already approved by a human with
green checks gets a lighter touch (nits → body, or skip; ask the user).

## 2. Load what the generic pass cannot know *(runner)*

Read, in this order, only the slices you need (`grep -n '^## ' file` first):
1. `.context/pr-reviews/<repo>.md` § "Known traps to check on every PR" and any section whose
   title matches a touched path (e.g. a model or table name). **This is the
   compounding KB** — every past review paid to verify these; check each applicable trap explicitly
   and record the result (hit / not applicable) for the triage sheet.
2. `.context/pr-reviews/README.md` § "Review workflow" and, for `dbt/models/**`, § "Architecture pass".
3. The repo's own review rules: its `CLAUDE.md` / `AGENTS.md` review and "PR requirements" sections —
   the bundle's `head/<path>` when the PR touched the file, else one live exception (§ Contract):
   `gh api contents … ?ref=<head>` — and the conventions in `.context/repos/<repo>.md`.
4. Open tickets the PR touches — keys matched by `tracker.key_regex` in the title/body: with
   `tracker.kind == jira` and `systems.jira`, the tool named by `tracker.mcp_tools.search` (JQL
   `key = <KEY>`, one call); with `tracker.kind == github`, the linked GitHub issue (`gh issue view`);
   plus any open `<KEY>` bug on the same column/vocabulary (grep `.context/INDEX.md` for the
   model/column name).

## 3. Review pass *(runner)*

Read `diff.patch` (and `files.json` for per-file stats). Default is one careful pass by this model,
collecting candidates per `scope.md` classes. `--deep` — **only** when the user passes it or the queue row
is over `deep_lines` (the `!` marker); a write-audit-publish/layer-placement/grant change is a reason to *suggest* `--deep` to the
user, not to trigger it — runs three lenses **in parallel** as `Agent` calls with `model: opus` spawned
by the runner (judgment work — not the Sonnet default), each given the bundle dir and one lens, returning
≤ 12 lines of `sev · file:line · claim · what in the bundle shows it` — claims only, no SQL, no fixes:
- design & data contracts (grain, keys, consumers, layer placement rules);
- failure modes (NULLs, timezones, idempotency, backfill races, write-audit-publish routing, secrets/grants);
- verification & maintainability (tests present and meaningful, CI coverage, docs, slop lens).
Then the runner verifies their claims in **one** batched pass — a lens finding without evidence you can restate is a question, not a finding.

## 4. Verify before you repeat *(runner)*

- Data claims (`systems.datalake` only; else mark them `unverified`) → run SQL (the datalake MCP); distribution checks beat existence checks; on token
  expiry ask the user to `/mcp` reauth. Check a timestamp column's actual type first — raw and modelled layers may type it differently.
  The **main session probes the token before spawning** (`select 1`; `reference/runner.md` § Spawn) so
  the runner never discovers it expired mid-review.
- CI state → `bundle.json.checks/status` as fetched; **never poll or read CI job logs** — report red/pending as such.
- Code/config baseline → `$CTX/base/<path>` (the file at the base sha, in the bundle); `gh api contents …?ref=`
  only for a file the PR did not touch; never the working tree.
- Convention claims → the documented spec (the schema/contract definitions the repo points
  to, dbt doc blocks, config reference), not a neighbouring precedent.
- Name the axis each ✅ covers ("logic verified against the manifest, not executed").
- Drop any candidate you cannot evidence; keep one line for the body if it is a natural question.

**Modelling PRs (`dbt/models/**`, BI-consumed tables, warehouse scripts that reshape data) — where
`systems.datalake` is on, the data pass is mandatory when the PR is complex** (`--deep` triggers, a curated/serving-layer model, a filter/join/
grain change, or a backfill). Rerun the numbers yourself in the datalake (its SQL MCP), never
accept the author's: row count at the declared grain before vs after (`count(*)` vs `count(distinct
<key>)`), distribution of every added/changed column, NULL-rate delta, and a sample of keys whose values
flip. A PR body number you could not reproduce is a Verification-gap finding. If the token is expired,
the runner marks those rows `unverified` and the main session asks the user to `/mcp` reauth, then
re-spawns one re-verify runner (`reference/runner.md`). (owner decision, 2026-09-19.)

## 4b. Stakeholder impact *(runner; modelling PRs, when feasible)*

Build the consumer map of every model/column the PR changes and write it to `$CTX/impact.json`
(`[{consumer, kind: dbt|bi|alert|export|other, owner, what_changes, rows_or_values_affected}]`):
- **dbt**: `grep -rn "ref('<model>')"` under `dbt/models` at the PR head (`gh api contents …?ref=`, the
  checkout is stale) + `exposures:` in the yml; note which of them select the changed columns.
- **BI tool** (`systems.datalake` only): the BI tool's MCP search for the model or table name, where one is connected — which
  workbooks/dashboards read it and who owns them. Skip with a `NOTE` line if the MCP is unavailable; never guess.
- **Alerts / exports**: alerting models, customer-export factories, reverse-ETL configs — anything that turns the column into a message or a file someone receives.
- Quantify: the SQL from step 4 gives "how many rows / which values change for consumer X".
Then: a change that alters what a known consumer reads and the PR body does not say so → **Stakeholder
impact** finding (`scope.md`), and the sheet's `NOTE` gets one `IMPACT:` line (consumers, owners, size).
No consumers found is also a result — record it in `impact.json` so the KB gains the consumer map (step 8).

## 5. Triage — overview, then walk every finding with the user (in the terminal, not on GitHub) *(main session)*

Same shape as `notion-page-review`: an overview first, then **one `AskUserQuestion` per batch of
4 findings**, so the user approves, rewrites, deep-dives or drops each item before anything is posted.
Numbers are assigned once and never change — the user refers to "#3" later.

**5a. Overview** (plain text, before the first question):
```
REVIEW SHEET <repo>#<pr> @ <head7> · mode <mode> · <author> · <title> · +A/-D F files
bot: <assessment> · humans: <…> · checks: <…> · traps checked: <n> (<hits>)
[<System> enrichment: n/a in this environment]   ← one per skipped gate
| # | sev | class | file:line | finding (one line) |
…
RECOMMEND: COMMENT | REQUEST_CHANGES | APPROVE-if-user-agrees
```
The `bot:` field is the `github.review_bot` verdict (omit it when that key is empty).
Order: STOP → WARN → NIT, file order within a severity. PR and tracker keys as clickable links (`tracker.url_template`); numbers
in the table, not prose. The runner has already written the sheet to `$CTX/triage.json` (`[{n, sev, class, path, line, side,
finding, evidence, comment, decision:null}]`) — print the overview from that file, never from the diff. The loop
survives a compaction only if the decisions land in that file after every batch.

**5b. The loop** — `AskUserQuestion`, 4 findings per call, sheet order:
- `header`: `"#<n> <sev>"` (≤ 12 chars; a revision becomes `"#<n> v2 <sev>"`).
- `question`: `file:line · <class>` line, blank line, `Finding:` one or two sentences with the
  evidence named (what was run / read to establish it), blank line, `Proposed inline comment:` the
  exact text in quotes, blank line, `Post this? (type a wording change or a question as Other)`.
- options, always these four, in this order: **Post (Recommended)** · **Deep dive** · **Body only**
  (drop the inline anchor, one softened line in the review body) · **Skip**. For a finding you
  recommend dropping yourself, put **Skip (Recommended)** first instead.
- **Deep dive** = the user wants more before deciding. Do the work now — run the SQL (`systems.datalake`), read the source
  at the tag, `git show origin/main:`, spawn an `opus` lens on that one question — then answer in
  the terminal (≤ 10 lines, evidence named) and **re-present the item as `vN` in the next batch**
  with its severity re-graded (up, down, or withdrawn). A deep dive can also be the user's question about
  the code itself; answer it the same way, the re-presented item then says what changed (nothing is fine).
- Any free-text answer = a rewrite or a redirect. Rewrite, re-present as `vN` in the next batch,
  never post an unseen rewrite. A redirect ("ask the author" — in Slack only where `systems.slack` / "open a ticket") leaves the
  loop and lands in `FF` (fast follows) at the end.
- After **each** batch: write the decisions into `$CTX/triage.json`, then ask the next batch in the
  same turn. Never post mid-loop — a GitHub review is one atomic POST (step 6).

**5c. Close the loop** — one last `AskUserQuestion`:
- `question`: the counts (posted / body-only / skipped / deep-dived), the **body draft** in full
  (summary line, body-only items, labels note, KB/FF pointers), and the recommended event with its
  reason per `scope.md` § Event recommendation.
- options: **COMMENT** · **REQUEST_CHANGES** · **APPROVE** · **Edit body** (types via Other). Flag the
  recommended one `(Recommended)`; APPROVE / REQUEST_CHANGES are never inferred (`scope.md`).
- `--post` (or "review and post") skips 5b/5c **only** for a COMMENT with no STOP finding; the
  overview is still printed.

## 6. Post exactly one review *(main session)*

Write `$CTX/request.json` (`{"event","body","comments":[{"path","line","side","body"}]}`; `line`
is the line number in the **new** file for `RIGHT`, old file for `LEFT`; it must be inside a
hunk of `diff.patch` or GitHub rejects the whole review — nothing half-posts). Then:
```sh
bash $BATON/skills/pr-review/scripts/submit-review.sh preview --repo <o/r> --pr <n> --head <full sha> --request $CTX/request.json
```
Show the preview (event, anchored comments, body, digest) — every item in it was approved in 5b/5c,
so this is a last look, not a second triage. One `AskUserQuestion`: **Submit (Recommended)** · **Abort**. On Submit:
```sh
bash $BATON/skills/pr-review/scripts/submit-review.sh submit … --confirm <digest>
```
The script refuses a closed/merged PR (set `PR_REVIEW_ALLOW_CLOSED=1` for a post-merge **COMMENT** with inline anchors; never APPROVE/REQUEST_CHANGES post-merge — a PR can merge mid-walk), a moved head, a digest mismatch, a path outside the diff, and a second submit
of the same digest; it appends `reviewed` to the ledger and prints `status: submitted · review
<id> · url`. `uncertain` → check GitHub before any retry. `failed` → fix the request (usually a
line outside a hunk) and preview again. The body gets no footer (config `footer` is empty by owner decision — attribution text only costs the reader) and the marker
`<!-- pr-review:v1 head=… base=… by=<login> -->` — that marker is how the next run knows the mode.

## 7. Thread replies (follow_up / replies_only) *(main session)*

`threads.json` lists every thread with `mine`, `last_author`, `id`. Draft replies per
`review-writing.md` § Replies, then run the **same loop as 5b**, 4 threads per `AskUserQuestion`:
- `header`: `"T<n> <who>"`; `question`: thread location + what the author said (quoted, ≤ 3 lines),
  blank line, `Proposed reply:` in quotes, blank line, `Reply?`.
- options: **Reply (Recommended)** · **Reply + resolve** (only offered on a thread we opened whose
  point is settled — otherwise omit it) · **Deep dive** · **Skip**. Free text = rewrite → `vN` next batch.
- Decisions land in `$CTX/replies.json` after every batch
  (`{"replies":[{"thread_id":"PRRT_…","body":"…","resolve":false}]}`); preview with
  `reply-threads.sh`, show the digest, submit on the user's go. The script refuses `resolve:true` on any
  thread we did not open (`dont-resolve-threads-pending-a-third-party`).

## 8. Flush — the review is not done until the KB grew *(main session)*

1. **KB write-back** (`.context/pr-reviews/<repo>.md`; create with
   `make -C $BATON/context-db new TYPE=pr-review DOMAIN=pr-reviews SLUG=<repo> TITLE="<org>/<repo> — PR review knowledge"`
   if missing). Add only **verified, reusable** facts — a trap to check on future PRs, a mechanic
   confirmed against the wheel/spec/data, a consumer map, a convention the repo docs do not state.
   Format: `- **<one-line rule>** — <how to check / why>. Verified on #<pr> (<date>).` Grep for an
   existing entry first and extend it rather than duplicate; a trap that fired again gets its
   "seen on" list extended, which is the signal it deserves a repo fix. Never store the PR
   narrative here (that is the session log) and never anything the repo's own docs already say.
   If the file passes 30KB, move narrative sections to `.context/archive/pr-reviews-<repo>-log.md`.
   Source every fact from `$CTX/triage.json` (`evidence`) and the walk decisions — the main session does
   not re-read the diff; a fact that needs re-verification goes to a one-question child.
2. `## Session log` line in the same file: `- <date> — #<pr> <author> <title> — <event>, <n>
   inline, <hits> traps hit, KB +<n>` (newest first).
3. Self-assessment drop: append one evidence-linked bullet to
   `.context/self-assessment/inbox/YYYY-Wnn--pr-reviews.md` (PR link, author, notable findings, outcome).
4. `make -C $BATON/context-db index`; `session-touch` if this session is registered.
5. If a fast follow needs a ticket, offer `ticket-open` (with the environment's backlog convention for architecture findings, where `environment.md` § Rules names one);
   never assign it to anyone.

## Auto-approve path for trivial PRs *(Sonnet `auto-runner` + main session; owner's standing decision 2026-09-19)*

Docs-only PRs and dependency **patch** bumps (minor for dev tooling only, major never) skip steps 3–5 when the
deterministic gate `scripts/trivial-check.py` passes (class, globs, size caps, CI green, the env config's `github.review_bot` green where
required and set, no human CHANGES_REQUESTED, 0 unresolved threads — config `auto_approve` in `.context/state/pr-review/config.json`).
`pr-scan` flags them `A`; the main session spawns `auto-runner` (`reference/runner.md` § `--auto`):
zero findings → `APPROVE` posted on the user's behalf via `submit-review.sh --auto` (which re-runs the gate on the exact
head and refuses unless `mode` is `live`) with a ≤3-line "Checked: …" body, or only logged
as `shadow_approve` (mode `shadow`, until the review date in `auto_approve.shadow_review_due`); any finding or doubt → `fallback`, and the
PR runs through the normal walk. This is the single exception to `scope.md` "APPROVE is never inferred"; the user
can switch it off with `auto_approve.mode: off`.

## Not in scope
Our own PRs (`pr-open`, `pr-watch`, `pr-event-brief`); merging (`pr-merge.sh` in `pr-watch`);
re-requesting the `github.review_bot`; review asks — Slack drafts only where `systems.slack` (`pr-open`).
