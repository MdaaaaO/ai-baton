---
name: ticket-update
description: "How a session posts a progress update on a tracker ticket (Jira or GitHub issues): one lean, dated DELTA comment (Win / Pivot / Next / Verified), label and status sync, and an optional durable pointer to the thread or PR; never a restatement of earlier comments. Invoke whenever a ticket step lands or direction changes."
metadata:
  version: "15"
  updated: "2026-09-30"
  reviewed: "2026-09-24"
  facts: "tracker.kind,tracker.mcp_tools.transitions_list,tracker.mcp_tools.remote_link,tracker.write_api,tracker.setting cloud_id"
user-invocable: true
---

# ticket-update — a ticket comment is a delta, not a re-statement

The comment stream on a ticket **is** its trace: reading top-to-bottom should tell the story with
no duplication. Each comment adds only what changed since the last. Precise over verbose — senior
readers get the TLDR line; detail only where it carries evidence.

**Pick the adapter first:** the resolved-profile block already in your SessionStart context names `tracker: kind=…`; once a compaction drops that block, or it never printed, fall back to `python3 $BATON/context-db/bin/kit_profile.py get tracker.kind` (from the workspace root). The grammar, cadence and flush below are tracker-neutral; the posting/sync mechanics
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
**Verified** — <claim> · <evidence>
```

- **Omit empty sections.** A quiet update may be just `🟢 … — **Next** …`. Don't scaffold blanks.
- **One line per section**; a bullet list only when there is genuinely >1 item; a Markdown table
  only when comparing numbers (counts / diffs / days); `code` for identifiers.
- **Pivot is the trace anchor** — every change of direction gets one, with the *why*, so a reader
  can scan *where* the work turned without reading every comment.
- **Pivot redraws the sketch — only when a marker is there.** The ticket carries a sketch marker
  (`docs/diagrams.md` § The sketch marker) **and** this Pivot moved WHERE it sits or WHAT its shape
  is → the Pivot section adds the before → after rendering (`docs/diagrams.md` § Rendering: Mermaid
  on GitHub, else a table/list) and re-stamps the Sketch with
  `python3 $BATON/skills/ticket-open/sketch.py stamp`, posting the new marker. No marker on the
  ticket → nothing changes here.
- **Verified is anchored to the system of record** — label it `(system of record)`: deploy
  imageTag/health, SQL result, CI check — never a proxy signal (`WORKSPACE.md` § Verification).
- **Every Verified claim carries an evidence item** — one line, `**Verified** — <claim> · <evidence>`,
  or a bulleted list when there is more than one claim: `**Verified**` then one `- <claim> ·
  <evidence>` per claim. `<evidence>` is a command with its output line (`` `cmd` → `output` ``), a
  URL, or a PR / commit reference (`#<n>`, `<owner>/<repo>#<n>`, a commit hash, or a PR URL) —
  `context-db/bin/evidence_check.py` checks that shape is there, never that the claim is true.
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

1. **Post via a file — both adapters run this, not just GitHub's.** Write the comment to a file, then run,
   in order: `python3 $BATON/context-db/bin/evidence_check.py <file>` — exit 0 → continue; exit 3 → fix the
   comment (add the missing evidence item(s), named on stderr) and re-check, never post as is; exit 2 → the
   file couldn't be read — fix the call or the file, never post. On GitHub, also run
   `python3 $BATON/context-db/bin/kit_profile.py public-text-check <file> --repo <repo>` — exit 0 → post;
   exit 1 → rewrite the hits generically (never post the file as is) and re-check; exit 2 → fix the call or
   the file, never post; exit 3 → do not post — the env store could not be loaded, so the check did not
   run; fix the store or check the file by hand before posting; a no-op when `<repo>` is one of
   `tracker.repos` or not public. Then post from that file, never by pasting the text: GitHub —
   `gh issue comment <n> -R <repo> --body-file <file>` (token per skill `gh-cli` —
   `github.sandbox_token_prefix`; `<repo>` from `tracker.repos`); Jira — the file's text as the body of
   `tracker.mcp_tools.comment`.
2. **Status** matches reality (adapter below). **Labels** stay honest (`ticket-open` § Labels / the
   repo's label set) — e.g. add `bug` once it turns out to be one, matching the PR label.
3. **Pointer (thread ↔ ticket, when feasible).** The ticket carries the discussion with a *pointer*,
   not a copy — a one-line `**Thread** <url>` / `**PR** <url>` in the comment is the sanctioned form for
   every adapter, no write scope needed. A tracker-native remote-link object (adapter below, where write
   access exists) is additional, never a substitute. No live bidirectional sync exists; never paste
   thread contents back and forth (and never a local `.context/` path).
   The opening block's own Links — epic/parent, related tickets — already live in the description (Jira) or
   body (GitHub); a DELTA comment adds a *new* pointer, it never restates them.
4. **Flush in lockstep.** The same delta goes to the context doc's Session log (`session-handoff`).
   The ticket is the team-facing trace; the context doc is the private handoff — keep them aligned,
   not identical (the ticket is not a mirror of `.context/`).

## Adapter — Jira (tracker.kind = jira)

- **Comment** per Sync — core § 1 (`tracker.mcp_tools.comment`). @-mentions need an ADF comment (a mention
  inside a markdown body is plain text to the tracker, nobody is notified) — rich markdown body + a
  one-line ADF cc underneath.
- **Status** via `tracker.mcp_tools.transition`: In Progress → **In Review**
  (`tracker.transitions.in_review`, when the PR is up — `pr-open` does this) → Done (`ticket-close`).
  Check the transitions actually available first via `tracker.mcp_tools.transitions_list` (ids drift).
- **Pointer** = a `**Thread** <url>` / `**PR** <url>` line in the comment (Sync — core § 3) — the sanctioned
  form here too; it needs no write scope and always lands. Only when `tracker.write_api` is also true, mirror
  it as a real remote issue link: first list the links already there via `tracker.mcp_tools.remote_link`
  (read-only) and stop if this URL is one of them, even when the read tool is missing (then read the links
  via `GET` on the same endpoint); otherwise create it with `POST /rest/api/3/issue/{key}/remotelink` —
  cloud id from the env fact `tracker.setting cloud_id` — body `{"object": {"url": "<link>", "title":
  "<title>"}}`. Check, then create: never create without checking. `tracker.write_api` false or unset (the
  common case — a read-only roster exposes only `getJiraIssueRemoteIssueLinks`, no write tool and no API
  token) → skip the POST silently, the comment line already carries the pointer.

## Adapter — GitHub issues (tracker.kind = github)

- **Comment** per Sync — core § 1 (`--body-file`). Mentions are plain `@login`. Edit-the-last-comment =
  `gh issue comment <n> --edit-last` under the same gate.
- **Status = labels + milestone** (no workflow states): `gh issue edit <n> -R <repo>
  --add-label/--remove-label/--milestone` from the repo's own label set; an open PR with
  `Closes #<n>` in its body is the "In Review" signal — no extra state to set.
- **Pointer** = `#<pr>` or the thread URL in the comment; GitHub cross-links PRs/issues itself.

## Not in scope
Creating the ticket → `ticket-open`. Closing it → `ticket-close`.
