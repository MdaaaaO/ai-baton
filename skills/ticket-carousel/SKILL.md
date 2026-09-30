---
name: ticket-carousel
description: "Turns candidates (a list, free text, or a repo/URL to mine) into tickets one at a time: draft, dedup, Submit / Merge / Skip, before any is created via ticket-open. Invoke when asked to turn a landscape read, retro or idea list into tickets."
metadata:
  version: "1"
  updated: "2026-09-30"
  reviewed: "2026-09-30"
  facts: "tracker.kind"
user-invocable: true
---

# ticket-carousel — nothing is created until the walk ends

Sessions that find several improvements either dump them as a list (nothing lands) or file them
straight away (the user never weighs them in, and duplicates of open work appear). This skill closes
that gap: draft every candidate, dedup it against open issues, then walk the cards with the user one
at a time before anything is created. The per-item walk is the same shape `pr-review`, `notion-page-review`
and `kit-health` already use — `docs/carousel.md` — not restated here.

**Pick the adapter first:** the resolved-profile block already in your SessionStart context names
`tracker: kind=…`; once a compaction drops that block, or it never printed, fall back to
`python3 $BATON/context-db/bin/kit_profile.py get tracker.kind`. Any other kind → say "no tracker
adapter for kind X in this environment" and stop.

## Intake

A ready list, free text, or a URL/repo to mine. A list or free text goes straight to Dedup. A URL/repo
goes through Mining (below) first. More than 12 real candidates from any source: ask once, before
drafting any of them, which to narrow to — never draft all of them unasked.

If the input does not already name a parent tracking issue / epic key, ask once, before drafting any
candidate, for it — a key, or "none". Carry the answer through to Create and Summary below; never ask
again mid-walk.

## Mining — the one forked step

Given a URL or repo instead of a ready list, fork the read: `Agent(subagent_type: "triage")` reads it
and returns at most 10 candidates of up to 3 lines each. Everything after this point runs inline, not
forked, because the walk below must call `AskUserQuestion` itself and a forked worker cannot
(`docs/carousel.md`).

## Dedup — deterministic first, the model only judges hits

Per candidate, take its 3–4 key terms (the distinctive nouns in its title) and search the tracker for
them before drafting: `gh issue list -R <repo> --search "<terms>" --state open` (GitHub) or the
tracker's search tool (Jira) — keep the top 3 hits. The model judges duplicate-or-not only among those
returned hits, never at large. A search that errors or times out is reported as a **failed search**,
exactly that — never folded into "no duplicate found"; retry once, then carry the candidate forward
unresolved and say so on its card.

## Draft

Use the repo's own issue template where it has one; otherwise `ticket-open`'s opening block (Goal +
Plan + Links + Sizing) — every draft carries a Sizing line regardless of template. On a GitHub tracker,
before a draft is ever shown, run the outgoing-text scan against the target repo (mirror `ticket-open`'s
GitHub adapter step): `python3 $BATON/context-db/bin/kit_profile.py public-text-check <file> --repo
<repo>` — exit 0 → show it; exit 1 → rewrite the hits generically (never show the flagged text) and
re-check; exit 2 → a bad `--repo` slug or an unreadable file — fix the call or the file, never show the
draft; exit 3 → say the check could not run and fix the store first, don't show an unchecked draft. On a
Jira tracker there is no `<owner>/<repo>` to pass — the ticket has no public surface, so skip this step.

## Walk — AskUserQuestion, 4 cards per call

One question per candidate, in the shape `docs/carousel.md` sets:
- `header`: `"<n> · <topic>"` (≤ 12 chars).
- `question`: the candidate's Goal line, blank line, `Draft:` + the rendered body as `preview`, blank
  line, `File this?`.
- options, in this order: **Submit** (recommended when dedup found nothing) · **Merge into #n** (shown,
  and recommended instead, only when dedup found a hit) · **Skip**.
- Any free-text answer is an edit: redraft from the note, then re-show the card once more in the next
  batch — never post a rewrite unseen.

## Create — nothing exists until the walk ends

A later card's Merge or Skip can change what an earlier Submit should have been (two candidates turn
out to be the same gap), so no issue is created mid-walk — not even when the user says "just file
them" to skip ahead; that only pre-answers every remaining card as Submit, it does not skip this gate.
Once every card has an answer:
1. Each **Submit** goes through `ticket-open` in full — labels, milestone, and, when Intake got a
   parent, the parent link plus a parent task-list line. With "none", skip both — there is nothing to
   link or add the line to.
2. Each **Merge into #n** posts exactly one comment on `#n`, in `ticket-update`'s DELTA grammar (a
   `**Win**` or `**Next**` line naming what the candidate added) — never a new issue.
3. Each **Skip** creates nothing; keep the user's stated reason, if one was given, for the summary.

## Summary

One line per candidate — created (`#<new>`), merged (`→ #<n>`), or skipped — with the user's reason
where they gave one. With a parent from Intake, this goes as one comment on that tracking issue (the
single write it gets from this run, not a `ticket-update` DELTA — there is no progress on the parent
itself, only this run's accounting). With "none", there is no issue to comment on: give the same
accounting in the chat reply instead.

## No tool — headless or scheduled runs

No `AskUserQuestion` available → create nothing; follow `docs/carousel.md` § No tool instead: end the
reply with a numbered `Decisions` block, one line per candidate, recommended option first, and list the
same candidates under `## Open decisions` in the session file, per that section, so the next
interactive session's walk starts from them. Write the drafts to the **stable**, per-user scratch dir —
`python3 $BATON/context-db/bin/kit_profile.py scratch --stable ticket-carousel` (mirrors how `pr-scan`
keeps its own output dir) — never the per-session default, which the next interactive session cannot
reach.

## Not in scope

Creating the approved ticket → `ticket-open` (this skill calls it, it does not reimplement placement,
labels or the parent link). Posting a routine progress update → `ticket-update`. The carousel's own
shape — batching, headers, previews — → `docs/carousel.md`, not restated here.
