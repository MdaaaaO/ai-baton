---
name: ticket-update
description: "How a session posts a progress update on a tracker ticket (Jira or GitHub issues): one lean, dated DELTA comment (Win / Pivot / Next / Verified), label and status sync, and an optional durable pointer to the thread or PR; never a restatement of earlier comments. Invoke whenever a ticket step lands or direction changes."
metadata:
  version: "8"
  updated: "2026-09-28"
  reviewed: "2026-09-24"
  facts: "tracker.kind,tracker.mcp_tools.transitions_list,tracker.mcp_tools.remote_link"
user-invocable: true
---

# ticket-update — a ticket comment is a delta, not a re-statement

The comment stream on a ticket **is** its trace: reading top-to-bottom should tell the story with
no duplication. Each comment adds only what changed since the last. Precise over verbose — senior
readers get the TLDR line; detail only where it carries evidence.

**Pick the adapter first:** `python3 $BATON/context-db/bin/kit_profile.py get tracker.kind` (from the
workspace root). The grammar, cadence and flush below are tracker-neutral; the posting/sync mechanics
follow **exactly one** adapter. Any other kind → say "no tracker adapter for kind X in this environment"
and stop.

## Comment grammar

One dated comment. A bold headline (≤1 line, em-dash lead — the scannable TLDR), then only the
sections that have something new. Never restate a prior comment.

```
🟢 2026-09-14 — <status headline>

**Win** — what landed (linked PR / commit / query result)
**Pivot** — ONLY when direction changed, and why   ← the "indication" marker; scan for these
**Next** — the next concrete step + who owns it
**Verified** — what was checked against the system of record, and how
```

- **Omit empty sections.** A quiet update may be just `🟢 … — **Next** …`. Don't scaffold blanks.
- **One line per section**; a bullet list only when there is genuinely >1 item; a Markdown table
  only when comparing numbers (counts / diffs / days); `code` for identifiers.
- **Pivot is the trace anchor** — every change of direction gets one, with the *why*, so a reader
  can scan *where* the work turned without reading every comment.
- **Verified is anchored to the system of record** — label it `(system of record)`: deploy
  imageTag/health, SQL result, CI check — never a proxy signal (`WORKSPACE.md` § Verification).
- **Links clickable** everywhere — keys rendered from `tracker.url_template`, PRs by full URL.
  Mentions follow the adapter.
- **Status glyphs, one leading:** 🟢 progressing · 🟡 waiting/soft-blocked · ⛔ blocked · 🔵 pivot · ✅ done.

## Cadence — post landed changes, not steps

Two overlapping comments an hour apart is noise. The stream carries one entry per state change a
*stakeholder reading the ticket* needs — not one per sub-step you finish.

- **Post the landed event, not the "about to."** "Re-approved, merging next" then "merged" 40 min
  later is **one** comment, posted at the merge. Only post an in-progress state when someone is
  *waiting on it* — a block, a handoff, a decision needed — where the status itself is the point.
- **If the next step will supersede this one in the same session/hour, wait and post once** — the
  reader wants the resolved state, not your keystrokes.
- **Superseding a recent comment = a tight delta, not a re-frame.** When an update lands what a
  recent comment listed as *Next*, add only the new fact and write `**Next** unchanged` (or just the
  one changed item). Don't restate the Win/Verified the prior comment already carried.
- **Verify only the NEW fact.** Don't re-verify a PR head or checks a prior comment already logged —
  the merge comment verifies the *merge commit* + final-head checks, not the build/tsc/signature the
  pre-merge comment already covered.
- **Rough floor:** if you posted <~2h ago and only your own incremental progress changed (nothing a
  reader must act on), fold it into the next real update instead of a new comment.

### Edit the last comment vs. post a new one

The gate is **not the clock** — it's *did notify-worthy state change, and has anyone engaged yet?*

- **Edit** your own most-recent comment (rather than post a new one) only when **all** hold: it's
  still the newest comment, nobody has replied/reacted to it, and **no notify-worthy state changed** —
  you're correcting or completing the *same* update: a wrong link, an empty section to drop, the
  merge-commit hash on a comment you posted *as* the merge. Flag the change visibly —
  `_(edited HH:MMZ — added merge sha)_` — so the trace isn't silently rewritten.
- **Post a new dated comment** whenever the state genuinely flipped — merged, blocked, unblocked,
  pivoted, a decision landed. These are *notify-worthy*: a new comment timestamps the flip and pings
  watchers; an edit does neither reliably and can orphan a reply already made. Editing is bookkeeping,
  never the way to record a state change.
- **Never edit to "keep the ticket current"** by rewriting an older comment into today's state — that
  erases the trace. The stream stays append-only; the current state is simply its newest entry.

## Sync — core

1. **Status** matches reality (adapter below). **Labels** stay honest (`ticket-open` § Labels / the
   repo's label set) — e.g. add `bug` once it turns out to be one, matching the PR label.
2. **Pointer (thread ↔ ticket, when feasible).** The ticket carries the discussion with a *pointer*,
   not a copy — a link to the thread or PR, or a one-line `**Thread** <url>` in the comment. No live
   bidirectional sync exists; never paste thread contents back and forth (and never a local
   `.context/` path).
3. **Flush in lockstep.** The same delta goes to the context doc's Session log (`session-handoff`).
   The ticket is the team-facing trace; the context doc is the private handoff — keep them aligned,
   not identical (the ticket is not a mirror of `.context/`).

## Adapter — Jira (tracker.kind = jira)

- Comment via `tracker.mcp_tools.comment`. @-mentions need an ADF comment (a mention inside a markdown
  body is plain text to the tracker, nobody is notified) — rich markdown body + a one-line ADF cc underneath.
- **Status** via `tracker.mcp_tools.transition`: In Progress → **In Review**
  (`tracker.transitions.in_review`, when the PR is up — `pr-open` does this) → Done (`ticket-close`).
  Check the transitions actually available first via `tracker.mcp_tools.transitions_list` (ids drift).
- **Pointer** = a remote issue link to the chat-thread permalink or PR — check what's already there via
  `tracker.mcp_tools.remote_link`; without that tool, fall back to `POST /rest/api/3/issue/{key}/remotelink`
  with body `{"object": {"url": "<link>", "title": "<title>"}}`.

## Adapter — GitHub issues (tracker.kind = github)

- Comment: `gh issue comment <n> -R <repo> --body-file <file>` (token per skill `gh-cli` —
  `github.sandbox_token_prefix`; `<repo>` from `tracker.repos`). Mentions are plain `@login`. Edit-the-last-comment =
  `gh issue comment <n> --edit-last` under the same gate as above.
- **Status = labels + milestone** (no workflow states): `gh issue edit <n> -R <repo>
  --add-label/--remove-label/--milestone` from the repo's own label set; an open PR with
  `Closes #<n>` in its body is the "In Review" signal — no extra state to set.
- **Pointer** = `#<pr>` or the thread URL in the comment; GitHub cross-links PRs/issues itself.

## Not in scope
Creating the ticket → `ticket-open`. Closing it → `ticket-close`.
