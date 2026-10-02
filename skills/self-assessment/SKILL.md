---
name: self-assessment
description: "Composes the user's weekly self-assessment for a past ISO week or range from `.context/` and the systems of record, never live sessions: the week file plus whatever report block/ledger card the env config asks for, back-filled as needed. Invoke when asked for \"self-assessment for last week\" or \"for W37\"."
metadata:
  version: "18"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
  facts: "self_assessment.ledger,self_assessment.ledger_url,self_assessment.sections"
user-invocable: true
---

# self-assessment — reconstruct a week from context, not from live sessions

A completed week is a **cold-source** task: everything needed to compose it is already durable —
in `.context/` (live docs + `archive/*-log.md`) and in the systems of record. A fresh session with
no memory of the week must be able to produce the same file. So this skill **does not depend on
any active session** — no peer pinging, no "what did you do this week?" messages. Peer drops are an
optional bonus if they happen to already exist; the source sweep is the authority.

The full charter (three purposes, fixed week-file format, initiatives model, contribution protocol)
lives in `.context/self-assessment/README.md` — **step 0: if it exists, read it for the format;
otherwise use the default scope from `self_assessment.scope` and continue** (`setup.sh` seeds the
charter from `$BATON/context-db/_templates/self-assessment-charter.md` on a fresh environment, but a
session must not block on it being there). **The default** without a charter: compose the week file
in the scaffold template's own section order (`make -C $BATON/context-db new TYPE=self-assessment` →
`_templates/self-assessment.md`); "work" is whatever
`self_assessment.scope` names — quote it at the top of the method per § Config-driven; there is no
initiatives model or contribution protocol to honour without the charter, so skip step 6's
initiatives gap-fill and inbox contribution rules. This skill *sequences the sourcing* and states the
rules a cold session gets wrong.

**Config-driven.** Read `self_assessment` first (`python3 $BATON/context-db/bin/kit_profile.py get
self_assessment`): `scope` is the one sentence that defines what counts as "work" this week — **quote it
at the top of the method** and drop anything outside it; `sources` gates each sweep in step 3 (a source
not in the list is skipped silently); `report` picks the deliverable in step 9; `ledger` (bool) gates step 10 — on
means this machine has the `Artifact` tool and wants every composed week on the update-ledger page, off means
step 10 is not applicable here; `ledger_url` is the page step 10 republishes (empty until the first run creates
it); `report_url` is the form the ledger links to (optional). A store without the `ledger` key reads as off —
`python3 $BATON/context-db/bin/kb.py config-set self_assessment.ledger true` turns it on.

## Arguments

- **Which week(s).** Default: the last **completed** ISO week (if today is W38 Monday, that's W37).
  Accept an explicit `YYYY-Wnn`, "last week", or a range "last 3 weeks" / `W35..W37`. For a range,
  run the checklist once per week, **oldest → newest** (later weeks reference earlier carry-overs).
- Weeks are **ISO** (Mon 00:00 → Sun 23:59 **UTC**), named by ISO week number.
- **`--backfill`** (or "back-fill", "there is no context for that week"): compose from the systems of record alone —
  see § Back-fill mode. The mode also switches on by itself when step 3's `.context/` date-grep returns zero hits
  for the window; say so in the method block either way.

## Checklist (per week)

1. **Fix the window and the boundary rule.** Compute `Mon YYYY-MM-DD 00:00Z → Sun YYYY-MM-DD 23:59Z`.
   - Include only what happened **inside** the window. Something merged/closed just after (e.g. the
     next Monday) is **excluded** and mentioned parenthetically as *(Wn+1)* if it gives context.
   - **State each item's status AS OF the window end, not as of now** — a PR "in review" on Sun that
     merged Tue is *"in review (merged Tue, W_n+1_)"*, not "merged". A cold session run weeks later
     gets this wrong without the rule.
   - **Retraction carve-out — corrections escape the boundary, facts don't.** A later
     retraction/correction of an in-window claim applies to the week *regardless of when it landed* —
     the week reports what turned out to be **true**, not what was believed Sunday night (a number that
     looked like a real finding on Sunday can turn out Tuesday to be a measurement confound, not a
     finding — "status as of window end" would have shipped it as a live result). Grep the days
     *after* the window on the week's items for retraction language (`retract`, `confound`,
     `was wrong`, `corrected`, `superseded`).
   - **Required output shape for cross-boundary hits.** The sweeps *will* surface items just outside
     the window (a tracker query bounded on `updated` catches next-week resolutions). Put them in a labelled
     **"Landed just after (W_n+1_)"** tail section — never silently merged into the week, never dropped.
2. **Read the previous week's file** (`weeks/YYYY-W{nn-1}.md`) for the exact format and any
   carry-overs, and the charter README for the fixed section list.
3. **Source sweep — the authoritative path (no live sessions needed):**
   - **GitHub** — only if `github` is in `self_assessment.sources`. **Three sweeps, not one**
     (prefix with `github.sandbox_token_prefix` when set): shipped (merged PRs, `gh pr list`/`gh search
     prs`), reviews given (`--reviewed-by`, then per-PR `/reviews` filtered to the window) and collab
     comments (`--commenter`, then per-PR/-issue `/comments` filtered to the window) — plus, when
     `tracker.kind == github`, issues opened/closed/commented in `tracker.repos`. Every sweep is a
     **pre-filter only**: the real window is a client-side check of each item's/comment's/review's own
     timestamp, never the search query alone, and `gh search prs --json` has no `mergedAt` field. Exact
     commands, the org-wide vs. tracker-restricted scoping, and the truncation check (`--limit 500` hit
     = re-run split by repo): `reference/github-sweep.md`.
   - **Jira** — only if `jira` is in `self_assessment.sources` (and `systems.jira`). Atlassian MCP JQL on
     `tracker.site`: `project = <tracker.project> AND (assignee was currentUser() OR reporter = currentUser())
     AND updated >= <Mon> AND updated <= <Sun>` — use created/resolved/transition dates, not just current
     status, to place each item in the right week.
   - **`.context/` history — always; sweep BOTH live docs AND `.context/archive/*-log.md`.** Since the 30KB
     guardrail (`make -C $BATON/context-db verify` warns past 30KB), a live doc keeps only the newest tail;
     **recent-but-not-newest history — including a just-finished week after a trim — lives in
     `archive/<slug>-log.md`** (newest-first, while the live Session log reads oldest-first: date-grep,
     never rely on position). Archive↔live pairs share the slug
     (`archive/<slug>-log.md` ↔ `<domain>/<slug>.md`). Date-grep both, using **the window's own year —
     step 1's Monday, never `` $(date +%Y) ``** (today's year is wrong for a January run composing last
     December or ISO W01; a silently-wrong year reads as "no history found", not an error). E.g. with
     `MON=<step 1's Monday YYYY-MM-DD>` and a window of Sep 7–13:
     `` grep -rEn "${MON%%-*}-09-(0[7-9]|1[0-3])" <dirs> `` (`${MON%%-*}` is the year portably — no
     `date -d`, GNU-only and absent on BSD/macOS `date`; adjust month/day to the window).
     **Exclude** generated files
     (`INDEX.md`, `SESSION_INDEX.md`) and `sessions/*.md` (live "responsibilities", not dated work);
     **dedupe** hits by (date + headline). Keep only docs inside `self_assessment.scope`.
   - **meetings / 1on1** — only if `meetings` / `1on1` are in `self_assessment.sources` (separate
     domains — sweep each listed one). Meeting notes are a **negative source too**: check whether a
     `## Outcome`/"fill in live" section was actually *filled* — an empty outcome on a meeting that ran
     (yet whose ticket was closed anyway) is itself a *Learnings* finding, not a skip. Where the report is
     `lattice` and `systems.lattice` is true, also read the review tool's bot DM (`WORKSPACE_SLACK_LATTICE_DM`, a
     `/plugin configure ai-baton` option or settings.local.json; needs `systems.slack`) for 1:1 recaps / action items landing in the window.
   - **on-call** — only if `on-call` is in `self_assessment.sources`: `on-call/rotations/<date>.md` + the
     `on-call/` monitor docs (+archive); **pr-reviews/README.md** + per-repo files (any environment).
   - **Datalake** — only if `datalake` is in `self_assessment.sources` and `systems.datalake`
     (the datalake's SQL MCP): verify any row-count / dollar-impact claim before it goes in.
   - *Optional bonus:* existing `inbox/YYYY-Wnn--*.md` drops are **leads for the *Collaboration* and
     *Learnings* sections only** — their real value is narrative/political context no system of record
     holds (an ownership ruling, who unblocked what); they are **never** a source for *Shipped* (that
     needs a tracker key / PR). Verify anything factual against the SoR, never copy blind. (Peer-pinging
     for fresh drops only makes sense for the *current, in-progress* week; for a past week the sources
     above are complete.)
4. **Verify, don't trust — and tie-break conflicts explicitly.** Numbers and tracker keys come from
   evidence, not memory or a drop's say-so; check anything surprising against the listed systems of
   record (`WORKSPACE.md` § Verification). Cross-sweep numeric conflicts are **routine, not rare**
   — apply a fixed tie-break, don't average or guess:
   - **PR line/file stats → GitHub** (the diff is authoritative over a ticket comment's recollection).
   - **Ticket-scoped measurements → that ticket's own closing comment** (row counts, per-event shares).
   - **Look for a derived/ratio figure in the same comment to arbitrate** before choosing — e.g. a line
     stating one figure as a multiple of the other pins which of two candidate numbers is right.
   - **If nothing arbitrates, drop the contested digit** rather than pick one — a vaguer true
     statement beats a precise wrong one.
5. **Compose `weeks/YYYY-Wnn.md`** in the charter's fixed format
   (`make -C $BATON/context-db new TYPE=self-assessment DOMAIN=self-assessment SLUG=YYYY-Wnn` if the file is
   new — it always scaffolds into `weeks/`, creating that folder the first time), then write it through the ctx
   tools (doc key `self-assessment/weeks/YYYY-Wnn`: `ctx_str_replace` / `ctx_insert`, or `ctx_create` with the
   whole text; a direct `Write`/`Edit` is denied). Set **`status: archived`** (`ctx_fm <key> status archived`)
   once the week is reconciled — a completed week is a closed record and this self-exempts it from
   the 30KB verify guardrail. A mid-week compose stays `status: active` and says **"week in progress — re-run
   Mon <date>"** in its banner; the Monday re-run folds the weekend, re-verifies every status claim and flips it.
   - **The report block's sections, in order and with their headings verbatim, come from
     `self_assessment.sections`** (`python3 $BATON/context-db/bin/kit_profile.py get self_assessment.sections`):
     an ordered list of headings set by the environment for its own review form. Empty or absent falls back to
     the kit's own four: `What I worked on`, `Next week`, `Blockers / risks`, `Is there anything else on your
     mind you'd like to share?`. The last section is the form's own free-text prompt: **1–3 sentences of
     reflection** — a learning, a concern about direction, a thank-you, a process observation — sourced from
     the week's *Learnings*, 1:1 notes and meeting outcomes. It never restates a shipped item or a blocker
     already listed; when the week gives nothing, write one plain sentence ("Nothing beyond the above this
     week.") rather than dropping the heading — the ledger card and the form both expect it.
6. **Index + initiatives.** Add/refresh the week's row in the README index; update (or, for
   initiatives no live session owns, gap-fill) the `initiatives/<slug>.md` arcs the week touched; a
   new multi-week effort gets a new initiative doc — all through the ctx tools, as in step 5.
7. **Move processed drops** to `inbox/archive/` (`ctx_move <key> <new key>`).
8. **`make -C $BATON/context-db verify`** — the final gate; the hooks already re-indexed (the week file should
   NOT appear in the >30KB warning — it's `status: archived`).
9. **Deliver per `self_assessment.report`:**
   - `lattice` (needs `systems.lattice`) — hand the user the paste-ready block at the end of the week file (the
     review tool has no connector; they paste it into the form its bot DM (`WORKSPACE_SLACK_LATTICE_DM`) links
     to). **No local `.context/` paths in that block** — it's an external surface (`CLAUDE.md`
     § Rules); refer to work by tracker key with clickable links. **Never infer submission state from the DM**
     — a nudge from the tool's own bot is not evidence of submission, and silence there means nothing.
   - `week-file` — the week file *is* the deliverable; no external block. Reply with a ≤5-line TLDR and
     the file path.
10. **Update the ledger — gated by `self_assessment.ledger`; when on, it is mandatory, every report mode, never
    a question.** `kit_profile.py get self_assessment.ledger` first: **off or absent → one line ("ledger: not
    enabled in this environment") and stop here** — never improvise a substitute page, file or gist. On → the
    ledger is one private Artifact page holding every composed week as a copy-ready card (copy button writes
    `text/html` + `text/plain` so links survive the paste; tick list in the viewer's browser), and skipping the
    card is the failure this step exists to prevent.
    1. `python3 $BATON/context-db/bin/kit_profile.py get self_assessment.ledger_url`.
    2. **URL set or empty** → build/republish/validate the page and derive the card's `tag`; the four
       sub-steps, exact commands and the reject-on-check-failure rule: `reference/ledger.md`.

## Back-fill mode — compose a week the context DB never saw

**Trigger:** `--backfill` given, or step 3's `.context/` date-grep returns zero hits for the window (then
say "switching to back-fill mode" in the reply and in the method block; a range runs oldest → newest as
usual). The systems of record become the **only** source, the week file stays the charter's fixed format,
and the week file carries **`provenance: back-fill`** in its frontmatter (steps 5–10 otherwise unchanged).
Per-source substitutes (GitHub commits, tracker comment-text search, chat, calendar, warehouse
verification-only) and the honesty/evidence-link rules: `reference/backfill.md`.

## Write-ownership (from the charter — don't clobber a concurrent composer)

This skill's session is the **sole composer**: only it writes `weeks/`, the README index/workflow,
and any external report block. If another session is already composing the same week (check `SESSION_INDEX.md`;
register yourself with `session-register`), agree ownership first — one composer per week. Never edit
another session's `inbox/` drop.

## Not in scope
Contributing a drop *to* a week (any session does that at the end of significant work — see README
§ Contribution protocol, write only your own `inbox/YYYY-Wnn--<initiative>.md`). Flushing an initiative's
own context doc → `session-handoff`.
