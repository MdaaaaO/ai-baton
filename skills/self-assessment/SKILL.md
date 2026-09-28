---
name: self-assessment
description: Compose the user's weekly self-assessment for a PAST ISO week or range from `.context/` and the systems of record, never live sessions: the week file plus the report block and ledger card the env config asks for, back-filled from the systems of record where `.context/` has no coverage. Invoke as "self-assessment for last week" or "for W37".
metadata:
  version: "12"
  updated: "2026-09-27"
  reviewed: "2026-09-24"
  facts: "self_assessment.ledger,self_assessment.ledger_url"
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
in the fixed section order the scaffold template already carries (`make -C $BATON/context-db new
TYPE=self-assessment` → `_templates/self-assessment.md` — Scope/Sources, Shipped, Collaboration &
reviews, Impact, Learnings, Next week, Blockers/risks, an optional Landed-just-after tail, and the
report block only where `self_assessment.report` is `lattice`); "work" is whatever
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
     the week reports what turned out to be **true**, not what was believed Sunday night (a pilot
     example: in-window traffic-drop projections retracted the next week were a NAT-gateway confound,
     not a finding — "status as of window end" would have shipped them as live results). Grep the days
     *after* the window on the week's items for retraction language (`retract`, `confound`,
     `was wrong`, `corrected`, `superseded`).
   - **Required output shape for cross-boundary hits.** The sweeps *will* surface items just outside
     the window (a tracker query bounded on `updated` catches next-week resolutions). Put them in a labelled
     **"Landed just after (W_n+1_)"** tail section — never silently merged into the week, never dropped.
2. **Read the previous week's file** (`weeks/YYYY-W{nn-1}.md`) for the exact format and any
   carry-overs, and the charter README for the fixed section list.
3. **Source sweep — the authoritative path (no live sessions needed):**
   - **GitHub** — only if `github` is in `self_assessment.sources`. **Three sweeps, not one**
     (prefix with `github.sandbox_token_prefix` when set). Scope: org-wide `--owner=<github.org>`; when
     `tracker.kind == github`, restrict to the repos in `tracker.repos` (one call per repo) and also
     sweep issues opened/closed/commented there (`gh search issues`) — they are the tracker.
     **`gh search prs --json` does not expose `mergedAt`** (its JSON fields are `assignees, author,
     authorAssociation, body, closedAt, commentsCount, createdAt, id, isDraft, isLocked, isPullRequest,
     labels, number, repository, state, title, updatedAt, url` — verify with `gh search prs --help` /
     `gh pr list --help` before trusting this on a new `gh` version); use `gh pr list` per repo instead,
     which does.
     - **shipped** (per repo in `tracker.repos`, tracker-restricted scope):
       `gh pr list -R <owner>/<repo> --state merged --search "merged:<Mon>..<Sun>" --author
       $WORKSPACE_GITHUB_LOGIN --json number,title,mergedAt,url --limit 100`. Org-wide scope (no
       `tracker.repos` restriction) has no per-repo `-R` to hang off, so fall back to `gh search prs
       --author=$WORKSPACE_GITHUB_LOGIN --owner=<github.org> --merged --merged-at <Mon>..<Sun> --json
       repository,number,title,state,createdAt,closedAt,url --limit 100` — `--merged` +
       `--merged-at` are input filters `gh search prs` does support; `closedAt` stands in for
       `mergedAt` (equal for a merged PR in practice).
     - **reviews given:** `gh search prs --reviewed-by=$WORKSPACE_GITHUB_LOGIN --owner=<github.org>
       --updated ">=<Mon>" --created "<=<Sun>" --json repository,number,title,url,updatedAt --limit 500` (add `--repo <r>`
       per repo instead of `--owner` in tracker-restricted scope) — `--updated` is a **pre-filter
       only** (the PR's *last* update, not when the review was given): a `--merged-at`/closed-date
       bound here would drop a review given in-window on a PR still open or merged the following
       week, so the update bound is one-sided (`>=<Mon>`); `--created "<=<Sun>"` is the lossless upper bound (an
       item created after Sunday cannot carry an in-window review or comment) that stops the net widening as
       the week ages — a range or back-fill run would otherwise hit the cap. Then per PR
       pull `/reviews` **and** `/comments` with pagination (30/page hides verdicts) and **keep only
       reviews whose own `submitted_at` falls inside `<Mon>..<Sun>`** — that per-review filter is the
       actual window, not the search query. The author sweep alone silently drops the whole
       *Collaboration & reviews* section.
     - **collab comments:** `gh search prs --commenter=$WORKSPACE_GITHUB_LOGIN --owner=<github.org>
       --updated ">=<Mon>" --created "<=<Sun>" --json repository,number,title,url,updatedAt --limit 500` (swap `--owner`
       for `--repo <r>` per repo in tracker-restricted scope) — again a pre-filter, since a PR touched
       after Sunday would otherwise be dropped by a two-sided `--updated <Mon>..<Sun>` bound; then pull
       each PR's `/comments` and **keep only comments whose own `created_at` falls inside
       `<Mon>..<Sun>`** (a PR can carry comments from many weeks). Same shape for issue comments:
       `gh search issues --commenter=$WORKSPACE_GITHUB_LOGIN --owner=<github.org> --updated ">=<Mon>" --created "<=<Sun>"
       --json repository,number,title,url,updatedAt --limit 500`, then per-issue `/comments` filtered
       the same way.
     - **issues opened/closed/commented** (tracker-restricted scope): `gh search issues
       --author=$WORKSPACE_GITHUB_LOGIN --repo <r> --updated ">=<Mon>" --created "<=<Sun>" --json
       repository,number,title,url,state,createdAt,closedAt --limit 500` per repo — pre-filter only;
       **keep only issues whose `createdAt` (opened) or `closedAt` (closed) falls inside
       `<Mon>..<Sun>`**. The "commented" leg is the `gh search issues --commenter` sweep above — run it with
       `--repo <r>` per repo in tracker-restricted scope, exactly like the PR sweep — so an issue opened weeks
       earlier and commented on in-window is caught there, not here. The one-sided sweeps carry `--limit 500` (`gh search` caps at 1000): a result
       count equal to the limit means the sweep was truncated — raise it or split by repo, never
       report from a capped list.
     - Filter every result to the window: `gh pr list`'s per-repo merged-PR query is already
       window-scoped by `--search "merged:…"` and needs no further filtering; every `--updated
       ">=<Mon>"` sweep above is a pre-filter only — the real window comes from the client-side
       per-item/per-comment/per-review timestamp check described with each sweep, never from the
       search query alone.
   - **Jira** — only if `jira` is in `self_assessment.sources` (and `systems.jira`). Atlassian MCP JQL on
     `tracker.site`: `project = <tracker.project> AND (assignee was currentUser() OR reporter = currentUser())
     AND updated >= <Mon> AND updated <= <Sun>` — use created/resolved/transition dates, not just current
     status, to place each item in the right week.
   - **`.context/` history — always; sweep BOTH live docs AND `.context/archive/*-log.md`.** Since the 30KB
     guardrail (`make -C $BATON/context-db verify` warns past 30KB), a live doc keeps only the newest tail;
     **recent-but-not-newest history — including a just-finished week after a trim — lives in
     `archive/<slug>-log.md`** (newest-first). Archive↔live pairs share the slug
     (`archive/<slug>-log.md` ↔ `<domain>/<slug>.md`). Date-grep both, built from step 1's Mon–Sun window
     (e.g. a window of Sep 7–13 is `` grep -rEn "$(date +%Y)-09-(0[7-9]|1[0-3])" <dirs> `` — `date` supplies
     the year so the example never goes stale; adjust the month/day range to the window). **Exclude** generated files
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
   record (`CLAUDE.md` § Verification). Cross-sweep numeric conflicts are **routine** (a pilot week had three)
   — apply a fixed tie-break, don't average or guess:
   - **PR line/file stats → GitHub** (the diff is authoritative over a ticket comment's recollection).
   - **Ticket-scoped measurements → that ticket's own closing comment** (row counts, per-event shares).
   - **Look for a derived/ratio figure in the same comment to arbitrate** before choosing — e.g. a
     "guardrail is ~7× the worst observed" line pins which of two worst-case digits is right (0.1 / 6.8 ≈ 0.0146%).
   - **If nothing arbitrates, drop the contested digit** rather than pick one — a vaguer true
     statement beats a precise wrong one.
5. **Compose `weeks/YYYY-Wnn.md`** in the charter's fixed format
   (`make -C $BATON/context-db new TYPE=self-assessment DOMAIN=self-assessment SLUG=YYYY-Wnn` if the file is
   new — it always scaffolds into `weeks/`, creating that folder the first time — else edit). Set **`status: archived`** in the
   frontmatter once the week is reconciled — a completed week is a closed record and this self-exempts it from
   the 30KB verify guardrail. A mid-week compose stays `status: active` and says **"week in progress — re-run
   Mon <date>"** in its banner; the Monday re-run folds the weekend, re-verifies every status claim and flips it.
   - **The report block has four sections, in this order and with these headings verbatim:** `What I worked on`,
     `Next week`, `Blockers / risks`, `Is there anything else on your mind you'd like to share?`. The fourth is
     the form's own free-text prompt: **1–3 sentences of reflection** — a learning, a concern about direction,
     a thank-you, a process observation — sourced from the week's *Learnings*, 1:1 notes and meeting outcomes.
     It never restates a shipped item or a blocker already listed; when the week gives nothing, write one plain
     sentence ("Nothing beyond the above this week.") rather than dropping the heading — the ledger card and
     the form both expect it.
6. **Index + initiatives.** Add/refresh the week's row in the README index; update (or, for
   initiatives no live session owns, gap-fill) the `initiatives/<slug>.md` arcs the week touched; a
   new multi-week effort gets a new initiative doc.
7. **Move processed drops** to `inbox/archive/`.
8. **`make -C $BATON/context-db index && make -C $BATON/context-db verify`** (the week file should NOT appear in the
   >30KB warning — it's `status: archived`).
9. **Deliver per `self_assessment.report`:**
   - `lattice` (needs `systems.lattice`) — hand the user the paste-ready block at the end of the week file (the
     review tool has no connector; they paste it into the form its bot DM (`WORKSPACE_SLACK_LATTICE_DM`) links
     to). **No local `.context/` paths in that block** — it's an external surface (`CLAUDE.md`
     § Rules); refer to work by tracker key with clickable links. **Never infer submission state from the DM**
     — the bot nudges with a button into the web form, silence there means nothing.
   - `week-file` — the week file *is* the deliverable; no external block. Reply with a ≤5-line TLDR and
     the file path.
10. **Update the ledger — gated by `self_assessment.ledger`; when on, it is mandatory, every report mode, never
    a question.** `kit_profile.py get self_assessment.ledger` first: **off or absent → one line ("ledger: not
    enabled in this environment") and stop here** — never improvise a substitute page, file or gist. On → the
    ledger is one private Artifact page holding every composed week as a copy-ready card (copy button writes
    `text/html` + `text/plain` so links survive the paste; tick list in the viewer's browser), and skipping the
    card is the failure this step exists to prevent.
    1. `python3 $BATON/context-db/bin/kit_profile.py get self_assessment.ledger_url`.
    2. **URL set** → `Artifact read` with that `url` and `path: index.html` (saves the live page locally), then
       `python3 $BATON/skills/self-assessment/ledger.py build --page <saved index.html> --week <the week file
       step 5 wrote> [--week …] --out <scratch>/ledger.html`. It replaces the card
       with the same id (a re-run updates in place), appends a new one, sorts by week and refreshes the masthead
       count + date range. Republish **with `url`** = the configured URL and the same `file_path` — publishing
       without `url` creates a duplicate page.
    3. **URL empty** (first run on this machine) → `ledger.py build --init --week <every week file in the domain> --out
       <scratch>/ledger.html --title "Update Ledger" --eyebrow "<team · user>" [--report-url <self_assessment.report_url>]`
       (template `ledger-template.html` next to the script), publish it as a new private Artifact (icon
       `calendar`), then `python3 $BATON/context-db/bin/kb.py config-set self_assessment.ledger_url <url>` so
       no later run creates a second page. The store is local — every environment gets its own ledger.
    4. `ledger.py check <html>` runs inside `build` (unique ids, ≥3 sections per card, no local paths in
       bullets, no unfilled placeholders); on a hit nothing is written to `--out` (the page lands in
       `<out>.rejected` for inspection) and the build exits non-zero — fix the week file, not the page.
    5. The card's `tag` derives from the week file's frontmatter: the sprint from the title, `composed <date>,
       week in progress — re-check after the Monday re-run` while `status: active`, `back-filled <date>` when
       `provenance: back-fill` is set (§ Back-fill mode). The final reply **links the ledger
       page and still pastes the block** — the page is the archive, the block is what the user pastes today.

## Back-fill mode — compose a week the context DB never saw

For weeks before the kit existed, weeks worked from another machine, or any window where step 3's
`.context/` date-grep returns nothing, the systems of record are the **only** source. The week file is
still the charter's fixed format; what changes is provenance and honesty about it.

- **Trigger:** `--backfill` given, or zero `.context/` hits for the window (then say "switching to back-fill
  mode" in the reply and in the method block). A range (`W29..W33`) runs oldest → newest as usual.
- **Sources (each gated by `self_assessment.sources` / `systems.*` exactly as in step 3):**
  - GitHub — the three sweeps (authored / reviewed-by / commenter) **plus** commits in the window
    (`gh search commits --author=$WORKSPACE_GITHUB_LOGIN --author-date=<Mon>..<Sun>`) and issue comments
    (`gh search issues --commenter=…`); PR bodies and review comments are the narrative you would otherwise
    have taken from the context doc — read them, don't just count them.
  - Tracker — the step-3 query **plus** the comments the user authored in the window: tracker query languages
    filter on comment *text*, not comment *author*, so read each hit's comment thread and keep the user's
    comments dated inside the window — the ticket's own closing comment is the measurement of record (step 4
    tie-breaks apply unchanged).
  - Chat (`systems.slack`) — the chat connector's search for the user's **own** messages in the window (one day
    of margin either side, threads included): the ownership rulings, unblocks and decisions no ticket records →
    *Collaboration* and *Learnings* leads only, never *Shipped*.
  - Calendar / meeting titles (`meetings` source; the calendar connector when attached) — attendance is a
    lead for *Collaboration*; an outcome needs a note or a ticket comment to be stated as fact.
  - Warehouse — only to **verify** a number that a ticket or PR claims, never to discover work.
- **Write it honestly.** The method block names the mode, lists which sources were available and which
  were not ("no chat in this environment", "calendar not attached"). *Learnings* opens with
  "reconstructed from systems of record; no contemporaneous notes" — a back-filled week can state what
  shipped and what was said, not what the user felt or intended, so the fourth report section stays to
  one plain sentence unless a 1:1 note or a message in the window supports more.
- **Every claim keeps its evidence link** (PR, ticket, message permalink) in the week file; the report block
  keeps tracker keys and PR links only, as always.
- **Seeding `.context/` is optional and asked once per run**, after the last week of the range: one
  `AskUserQuestion` offering to gap-fill `initiatives/<slug>.md` arcs from the back-filled weeks (yes →
  step 6 for each; no → leave the initiatives untouched, the week files alone are the record). Never seed
  initiative context docs (`TYPE=epic`) from a back-fill — those belong to the sessions that do the work.
- Steps 5–10 run unchanged, with one frontmatter addition: the week file carries **`provenance: back-fill`**
  (that key, not prose, is what tags the ledger card `back-filled <date>`). Then `status: archived` for a
  completed week, index row, drops, `make index && verify`, the report block, and — where the ledger is on —
  the card.

## Write-ownership (from the charter — don't clobber a concurrent composer)

This skill's session is the **sole composer**: only it writes `weeks/`, the README index/workflow,
and any external report block. If another session is already composing the same week (check `SESSION_INDEX.md`;
register yourself with `session-register`), agree ownership first — one composer per week. Never edit
another session's `inbox/` drop.

## Not in scope
Contributing a drop *to* a week (any session does that at the end of significant work — see README
§ Contribution protocol, write only your own `inbox/YYYY-Wnn--<initiative>.md`). Flushing an initiative's
own context doc → `session-handoff`.
