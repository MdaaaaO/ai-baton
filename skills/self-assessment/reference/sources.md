# Source sweep — the non-GitHub sources behind step 3

Loaded from `SKILL.md` step 3. Each source runs only when it is listed in `self_assessment.sources`
(`.context/` history always runs). GitHub has its own file: `reference/github-sweep.md`. No live
session is needed for any of these — peer-pinging for fresh drops only makes sense for the *current,
in-progress* week; for a past week the sources below are complete.

## .context history

- Sweep **both** live docs **and** `.context/archive/*-log.md`. `make -C $BATON/context-db verify` warns
  past 30 KB (64 KB for `type: reference`/`repo`), so a live doc keeps only its newest tail;
  **recent-but-not-newest history — including a just-finished week after a trim — lives in
  `archive/<slug>-log.md`**.
- The archive log is newest-first while a live Session log reads oldest-first: date-grep, never rely on
  position. Archive↔live pairs share the slug (`archive/<slug>-log.md` ↔ `<domain>/<slug>.md`).
- Use **the window's own year** (step 1's Monday): today's year is wrong for a January run composing last
  December or ISO W01, and a silently-wrong year reads as "no history found", not an error. `${MON%%-*}`
  is the year portably — `date -d` is GNU-only and absent on BSD/macOS `date`. Adjust month/day to the window.
- **Exclude** generated files (`INDEX.md`, `SESSION_INDEX.md`) and `sessions/*.md` (live
  "responsibilities", not dated work). **Dedupe** hits by (date + headline). Keep only docs inside
  `self_assessment.scope`.

## Jira

Only with `jira` listed (and `systems.jira`). Atlassian MCP JQL on `tracker.site`:
`project = <tracker.project> AND (assignee was currentUser() OR reporter = currentUser()) AND updated >= <Mon>
AND updated <= <Sun>` — use created/resolved/transition dates, not just current status, to place each item in
the right week.

## Meetings and 1on1

Separate domains — sweep each listed one. Meeting notes are a **negative source too**: check whether a
`## Outcome`/"fill in live" section was actually *filled* — an empty outcome on a meeting that ran (yet whose
ticket was closed anyway) is itself a *Learnings* finding, not a skip. Where the report is `lattice` and
`systems.lattice` is true, also read the review tool's bot DM (`WORKSPACE_SLACK_LATTICE_DM`, a
`/plugin configure ai-baton` option or settings.local.json; needs `systems.slack`) for 1:1 recaps / action
items landing in the window.

## On-call

`on-call/rotations/<date>.md` + the `on-call/` monitor docs (+archive); **pr-reviews/README.md** + per-repo
files (any environment).

## Datalake

Only with `datalake` listed and `systems.datalake` (the datalake's SQL MCP): verify any row-count /
dollar-impact claim before it goes in.

## Inbox drops

Optional bonus: existing `inbox/YYYY-Wnn--*.md` drops are **leads for the *Collaboration* and *Learnings*
sections only** — their value is narrative context no system of record holds (an ownership ruling, who
unblocked what); they are **never** a source for *Shipped* (that needs a tracker key / PR). Verify anything
factual against the system of record, never copy blind.
