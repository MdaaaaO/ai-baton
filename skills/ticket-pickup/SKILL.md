---
name: ticket-pickup
description: "Verifies a ticket's Sizing line against the default branch, decides main session vs a background sub-agent and model, sizes a missing line, and claims the ticket. Use when picking up, starting or resuming a tracker ticket. Not for creating one (`ticket-open`) or posting a progress update (`ticket-update`)."
metadata:
  version: "2"
  updated: "2026-09-30"
  reviewed: "2026-09-27"
  facts: "tracker.kind"
user-invocable: true
argument-hint: "<ticket>"
---

# ticket-pickup — verify the sizing, then launch the right worker

`ticket-open` makes the model and delegation call once, when a ticket is written, so it never has to be
re-derived from scratch at pickup. This skill is the other end: it reads that call back, checks it still
holds, and acts on it. Inline, not forked — it decides and launches, so its reasoning has to stay in the
main session's turn.

**Pick the adapter first:** `python3 $BATON/context-db/bin/kit_profile.py get tracker.kind` (from the
workspace root). Follow **exactly one** adapter below. Any other kind → say "no tracker adapter for
kind X in this environment" and stop — never improvise.

## 1. Read the ticket

Open the ticket (adapter below) and run `python3 $BATON/context-db/bin/sizing.py parse <file>` on its body
(save it to a scratch file first, or pipe it in with `-`). Exit 0 → the parsed `model, decision, reason`.
Exit 1 → no Sizing line at all: go to § 4. Exit 2 → a line is there but malformed (a model outside `sonnet` /
`haiku` / `opus`, or the wrong shape): say so, quoting the raw line, and treat it as missing (§ 4).

## 2. Verify it still holds

A Sizing line is a claim made when the ticket was written; the default branch may have moved since. Check the
evidence lines the ticket cites and the state of any Plan steps against the current default branch (a quick
`grep`/`git log`, not a full read of every file):

- A cited symptom that no longer reproduces, or a Plan step the default branch already implements → narrow the
  plan and say which step(s) drop out, before deciding anything else.
- Nothing changed → the Sizing line's call stands; carry its `reason` forward unchanged.
- The ticket's shape changed enough that the original sizing no longer fits (e.g. every Plan step but one is
  already done) → re-size using the table in `docs/delegation.md` § Sizing, and say why the call changed.

## 3. Decide — main session or a worker, and the model

`decision` from § 1/§ 2 is `delegate` or `main session`; `model` is the size. Default to it. Override only when
§ 2 changed the picture, or the ticket is now a **judgment** call (`docs/delegation.md` § Sizing table) that a
`sonnet` line missed — escalate to `opus` rather than let an uncertain fix land unreviewed.

- **Main session:** do the work here; nothing more to launch.
- **Delegate:** build the worker brief from `docs/delegation.md` § The worker brief (setup line, the issue and
  what "done" means, the fail-on-main test proof, the hard rules, the one-commit + push shape, the stop line,
  what to return) and launch it with `Agent`, `model` set explicitly to the sized model, in the **background**
  — the picking-up session keeps going (the registry, another ticket, answering the user) while it runs.

## 4. A missing or malformed Sizing line

Size it now — same table, same reasoning `ticket-open` would have used — and write it back (adapter below) so
the next pickup reads it directly instead of re-deriving it. Say the line you added and why.

## 5. Claim it

Where the tracker supports it, mark the ticket picked up before starting (adapter below) — so a second session
scanning the same tracker does not start the same ticket twice.

## Adapter — Jira (tracker.kind = jira)

1. **Read** the description and the comments via the tracker's read tool. `ticket-open` writes the Sizing
   line into the description, so parse the description first; only fall back to the comments (oldest
   first) for tickets opened before this change, where the block still landed in an opening comment. The
   last line found still wins.
2. **Write a sized/corrected line** as a new comment (`tracker.mcp_tools.comment`), produced by
   `sizing.py format`, never hand-rolled. It overrides the earlier line because the last one wins.
3. **Claim** by transitioning to the environment's in-progress state (`tracker.transitions`, if the env config
   names one) and assigning the current user.

## Adapter — GitHub issues (tracker.kind = github)

Tools: `gh` with the token per skill `gh-cli` (`github.sandbox_token_prefix`), in one of `tracker.repos`.

1. **Read:** `gh issue view <n> -R <repo> --json body,comments`. `ticket-open` writes the Sizing line in the
   body, or in a first comment when it fell back to `gh issue comment`. Pass the body followed by the
   comments, oldest first, to `sizing.py parse`; the last line wins.
2. **Write a sized/corrected line** as a new comment:
   `python3 $BATON/context-db/bin/sizing.py format <model> "<delegate|main session>" "<reason>"`, posted with
   `gh issue comment <n> -R <repo> --body-file <file>`. It overrides the earlier line because the last one
   wins.
3. **Claim:** `gh issue edit <n> -R <repo> --add-assignee @me`; add the repo's in-progress label too if one
   exists in its label set (`gh label list -R <repo>`) — don't invent one.

## Not in scope

Creating the ticket (the Sizing line's first write) → `ticket-open`. Posting progress once picked up →
`ticket-update`. Closing it → `ticket-close`.
