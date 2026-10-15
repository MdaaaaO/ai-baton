---
name: self-assessment
description: "Composes the user's weekly self-assessment for a past ISO week or range from `.context/` and the systems of record, never live sessions: the week file plus whatever report block/ledger card the env config asks for, back-filled as needed. Invoke when asked for \"self-assessment for last week\" or \"for W37\"."
metadata:
  version: "21"
  updated: "2026-10-15"
  reviewed: "2026-10-15"
  facts: "self_assessment.ledger,self_assessment.ledger_url,self_assessment.sections"
user-invocable: true
---

# self-assessment — reconstruct a week from context, not from live sessions

A completed week is a **cold-source** task: everything needed is already durable — in `.context/` (live docs +
`archive/*-log.md`) and in the systems of record. A fresh session with no memory of the week must produce the same
file. So this skill **does not depend on any active session** — no peer pinging; existing peer drops are an
optional bonus, the source sweep is the authority.

**Step 0 — the charter.** The full charter (week-file format, initiatives model, contribution protocol) lives in
`.context/self-assessment/README.md`: if it exists, read it for the format; otherwise compose in the scaffold
template's own section order (`make -C $BATON/context-db new TYPE=self-assessment` → `_templates/self-assessment.md`),
skip step 6's initiatives gap-fill and the inbox contribution rules, and continue — never block on the charter.

**Config-driven.** Read `self_assessment` first (`python3 $BATON/context-db/bin/kit_profile.py get self_assessment`):
`scope` is the one sentence that defines "work" — **quote it at the top of the method** and drop anything outside
it; `sources` gates each sweep in step 3 (a source not listed is skipped silently); `report` picks the deliverable
in step 9; `ledger` (bool, absent = off) gates step 10; `ledger_url` is the page step 10 republishes;
`report_url` is the form the ledger links to (optional). Key detail: `reference/composing.md` § Config keys.

## Arguments

- **Which week(s).** Default: the last **completed** ISO week. Accept `YYYY-Wnn`, "last week", or a range
  ("last 3 weeks" / `W35..W37`) — run the checklist once per week, **oldest → newest** (later weeks reference
  earlier carry-overs).
- Weeks are **ISO** (Mon 00:00 → Sun 23:59 **UTC**), named by ISO week number.
- **`--backfill`** (or "there is no context for that week"): compose from the systems of record alone — § Back-fill
  mode. It also switches on by itself when step 3's `.context/` date-grep returns zero hits; say so in the method
  block either way.

## Checklist (per week)

1. **Fix the window and the boundary rule.** `Mon YYYY-MM-DD 00:00Z → Sun YYYY-MM-DD 23:59Z`. Include only what
   happened **inside** it; **state each item's status AS OF the window end**, not as of now ("in review (merged
   Tue, W_n+1_)"). Corrections escape the boundary, facts don't: grep the days *after* the window for retraction
   language on the week's items. Items just outside the window go in a labelled **"Landed just after (W_n+1_)"**
   tail — never merged in, never dropped. Detail: `reference/composing.md` § Window boundary.
2. **Read the previous week's file** (`weeks/YYYY-W{nn-1}.md`) for the exact format and carry-overs, and the
   charter README for the fixed section list.
3. **Source sweep — the authoritative path**, each source only when listed in `self_assessment.sources`:
   - **GitHub** — three sweeps (shipped, reviews given, collab comments), plus tracker issues when
     `tracker.kind == github`; every search is a pre-filter only, the window is a client-side timestamp check:
     `reference/github-sweep.md`.
   - **`.context/` history — always; BOTH live docs AND `.context/archive/*-log.md`** (a trimmed live doc keeps
     only its newest tail). Date-grep both with **the window's own year — step 1's Monday, never
     `` $(date +%Y) ``**: with `MON=<step 1's Monday YYYY-MM-DD>`,
     `` grep -rEn "${MON%%-*}-09-(0[7-9]|1[0-3])" <dirs> `` for a window of Sep 7–13 (`${MON%%-*}` is the year
     portably — no `date -d`). Exclusions, dedupe and the archive↔live pairing: `reference/sources.md` § .context history.
   - **Jira, meetings / 1on1, on-call, datalake, inbox drops**: `reference/sources.md` (a meeting's empty
     outcome is itself a finding; drops are leads for *Collaboration*/*Learnings* only, never for *Shipped*).
4. **Verify, don't trust — and tie-break conflicts explicitly.** Numbers and tracker keys come from evidence
   (`WORKSPACE.md` § Verification). Cross-sweep numeric conflicts are routine: PR stats → GitHub; ticket-scoped
   measurements → that ticket's closing comment; a derived ratio in the same comment arbitrates; nothing
   arbitrates → drop the contested digit (`reference/composing.md` § Tie-break).
5. **Compose `weeks/YYYY-Wnn.md`** in the charter's fixed format (`make -C $BATON/context-db new
   TYPE=self-assessment DOMAIN=self-assessment SLUG=YYYY-Wnn` if new), written through the ctx tools (doc key
   `self-assessment/weeks/YYYY-Wnn`; a direct `Write`/`Edit` is denied). Set **`status: archived`** (`ctx_fm <key>
   status archived`) once reconciled; a mid-week compose stays `status: active` with a **"week in progress —
   re-run Mon <date>"** banner (`reference/composing.md` § Week file). The report block's headings come verbatim, in order, from
   `self_assessment.sections` (empty → the kit's four; the last is 1–3 sentences of reflection, never dropped):
   `reference/composing.md` § Report sections.
6. **Index + initiatives.** Add/refresh the week's row in the README index; update (or, for initiatives no live
   session owns, gap-fill) the `initiatives/<slug>.md` arcs the week touched; a new multi-week effort gets a new
   initiative doc — all through the ctx tools.
7. **Move processed drops** to `inbox/archive/` (`ctx_move <key> <new key>`).
8. **`make -C $BATON/context-db verify`** — the final gate (the archived week file must NOT appear in the >30KB
   warning).
9. **Deliver per `self_assessment.report`:**
   - `lattice` (needs `systems.lattice`) — hand the user the paste-ready block at the end of the week file (they
     paste it into the form the review tool's bot DM links to). **No local `.context/` paths in that block** —
     an external surface; refer to work by tracker key with clickable links. **Never infer submission state from
     the DM** (`reference/composing.md` § Delivery).
   - `week-file` — the week file *is* the deliverable; reply with a ≤5-line TLDR and the file path.
10. **Update the ledger — gated by `self_assessment.ledger`; when on, mandatory in every report mode, never a
    question.** Off or absent → one line ("ledger: not enabled in this environment") and stop — never improvise a
    substitute page, file or gist. On → read `self_assessment.ledger_url`, then build, republish and validate the
    one private Artifact page of copy-ready week cards: `reference/ledger.md`.

## Back-fill mode — compose a week the context DB never saw

**Trigger:** `--backfill`, or step 3's `.context/` date-grep returns zero hits (say "switching to back-fill mode"
in the reply and the method block). The systems of record become the **only** source, the week file keeps the
charter's format and carries **`provenance: back-fill`** in its frontmatter (steps 5–10 unchanged). Per-source
substitutes and the honesty/evidence-link rules: `reference/backfill.md`.

## Write-ownership — one composer per week

This skill's session is the **sole composer**: only it writes `weeks/`, the README index/workflow and any external
report block. If another session is already composing the same week (`SESSION_INDEX.md`; register with
`session-register`), agree ownership first. Never edit another session's `inbox/` drop.

## Not in scope

Contributing a drop *to* a week (any session writes only its own `inbox/YYYY-Wnn--<initiative>.md`, README §
Contribution protocol). Flushing an initiative's own context doc → `session-handoff`.
