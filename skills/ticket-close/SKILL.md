---
name: ticket-close
description: Checklist for closing a ticket in the environment's tracker (Jira or GitHub issues) cleanly — a final outcome comment (Delivered / Verified / Out-of-scope), the right transition/resolution or close reason (Done / Won't Do / Cancelled), confirmation the delivering PRs are linked, and the context-doc flush. Invoke when a ticket's work is finished, decided-against, or abandoned.
metadata:
  version: "6"
  updated: "2026-09-27"
  reviewed: "2026-09-24"
  facts: "tracker.kind,tracker.close_reasons"
user-invocable: true
---

# ticket-close — leave a ticket a reader can trust cold

A closed ticket should let anyone reconstruct what shipped and what was deliberately left, from the
ticket alone. One final comment, the correct transition, links intact.

**Pick the adapter first:** `python3 $BATON/context-db/bin/kit_profile.py get tracker.kind` (from the
workspace root). Follow **exactly one** adapter below. Any other kind → say "no tracker adapter for
kind X in this environment" and stop.

## Core — every tracker

1. **Final comment** — a recap, the one place a fuller-than-delta comment is warranted:
   ```
   ✅ 2026-09-14 — Closing: <outcome in one line>

   **Delivered** — what shipped under this ticket (PRs + sub-tasks, all clickable)
   **Verified** — the final check against the system of record (numbers table if applicable)
   **Out of scope / left as is** — anything deliberately not done, so it doesn't read as missed
   ```
   Evidence-dense, not narrative. Links rendered from `tracker.url_template`.
2. **Confirm delivering PRs are linked** and merged; don't resolve a PR review thread that's waiting
   on a third party (`WORKSPACE.md` § Rules — the open thread is the gate).
3. **Close with the right outcome** — one of three, each a key under the adapter's config:
   shipped → `done`; decided-not-to-do on merit → `wont_do`; abandoned / superseded → `cancelled`.
4. **Flush + close out** — run `session-handoff`: context-doc Session log + relevant section, tick
   `.context/reference/priorities.md`, archive the context doc if the initiative is fully done, update
   `MEMORY.md` if a note changed.

## Adapter — Jira (tracker.kind = jira)

- Final comment via `tracker.mcp_tools.comment`.
- **Transition + resolution** via `tracker.mcp_tools.transition` (check `getTransitionsForJiraIssue`
  first — ids drift):
  - Shipped → **Done** (`tracker.transitions.done`).
  - Won't Do → `tracker.transitions.wont_do`; if only Done is exposed, transition Done then
    `editJiraIssue` `{"resolution":{"name":"Won't Do"}}` and retitle `(won't do — reason)`.
  - Cancelled → `tracker.transitions.cancelled`.

## Adapter — GitHub issues (tracker.kind = github)

`gh` with the token per skill `gh-cli` (`github.sandbox_token_prefix`), in one of `tracker.repos`.

- Final comment: `gh issue comment <n> -R <repo> --body-file <file>`.
- **Close reason replaces the transition.** Resolve the value for the outcome key and **stop if the lookup
  fails** — an empty `--reason` would close the issue as `completed` with no error:
  `reason=$(python3 $BATON/context-db/bin/kit_profile.py get tracker.close_reasons.<done|wont_do|cancelled>) || { echo "close reason missing — run python3 $BATON/context-db/bin/kb.py migrate"; exit 1; }`
  then pass it **quoted**: `gh issue close <n> -R <repo> --reason "$reason"`. GitHub knows only two reasons, so
  `done` → `completed` and both `wont_do` and `cancelled` → `not planned` (the value has a space; `not_planned` is
  rejected by `gh`, and a store still carrying it is fixed by `kb.py migrate`). For `wont_do`, also retitle
  `(won't do — reason)` (`gh issue edit --title`) so it doesn't read as cancelled.
- **Parent:** tick this issue's `- [ ] #<n>` line on the parent/tracking issue (`gh issue edit` with
  the re-read body); close the parent too if this was its last open child.
- A PR body carrying `Closes #<n>` closes it on merge as `completed` — still post the final comment.

## Not in scope
Creating → `ticket-open`. Progress updates → `ticket-update`. The PR merge itself →
`sign-queue` (`systems.signed_commits` environments) / `pr-watch`.
