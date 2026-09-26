---
name: ticket-open
description: Checklist every session follows when it CREATES a ticket in the environment's tracker (Jira or GitHub issues) — type/placement (active sprint, or labels + milestone), labels, epic/parent link, a lean opening comment (Goal + Plan + Links), and the matching .context/ context doc. The tracker analogue of pr-open. Invoke right before/after creating the ticket.
metadata:
  version: "5"
  updated: "2026-09-26"
  reviewed: "2026-09-24"
user-invocable: true
---

# ticket-open — what "the ticket is open" means here

Create a ticket with its fields already right and one lean opening comment, so the board and
every session can read intent at a glance. Mirrors `pr-open`. The shared mechanics live in the
env config / `.context/reference/environment.md` — this skill *sequences* them, it does not
restate them.

**Pick the adapter first:** `python3 $BATON/context-db/bin/kit_profile.py get tracker.kind` (from the
workspace root). Follow **exactly one** adapter below. Any other kind → say "no tracker adapter for
kind X in this environment" and stop — never improvise.

## Core — every tracker

1. **Create it placed** — in the current planning bucket, typed, labelled (adapter steps).
2. **Link it** to its epic / parent and related tickets. Every key in the body/comment is clickable,
   rendered from `tracker.url_template`.
3. **One lean opening comment** — intent, not an essay (grammar lives in `ticket-update`):
   ```
   **Goal** — one line: what this delivers and why
   **Plan** — 2–4 terse steps
   **Links** — epic/parent · related tickets · any existing PR (all clickable)
   ```
4. **Context doc.** If this ticket is its own initiative, create the context doc
   (`make -C $BATON/context-db new TYPE=epic DOMAIN=<domain> SLUG=<key-slug>`); if it belongs to an existing
   epic, add it under that epic's context doc instead. Never cite the local `.context/` path on the
   ticket (`WORKSPACE.md` § Rules).
5. **Register / coordinate.** If another live session owns the epic (`.context/SESSION_INDEX.md`),
   agree ownership before starting work on it. Flush the new ticket to the context doc.

## Adapter — Jira (tracker.kind = jira)

Tools: `tracker.mcp_tools.create` / `.comment`; project `tracker.project`; links
`tracker.url_template` with `{key}`.

1. **Create in the active sprint.** `tracker.mcp_tools.create` with `tracker.sprint_field` = the open
   sprint whose name starts with `tracker.board_sprint_prefix` and whose dates contain today — the
   sprint-boundary / sub-task / Unplanned-flag (`tracker.unplanned_field`) rules are in
   `.context/reference/environment.md` § Rules. Correct issue type: Task under
   an Epic; Sub-task under a Task (a Task can't parent a Task; sub-tasks reject the sprint field and
   inherit the parent's). Where `environment.md` § Rules names a backlog exception (a summary prefix or
   label for deep-review follow-ups), those tickets skip the sprint — say so.
2. **Labels — two axes, from the ticket vocabulary** (§ Labels below). Set at creation via
   `additional_fields.labels`. Stack any program/domain tags; pick **exactly one** work-type. Keep
   `bug`/`documentation`/`data-migration` spelled identically to the PR labels so ticket and PR
   carry matching signal.
3. **Link it.** Epic link / parent on create; related tickets via `createIssueLink`.
4. **Opening comment** via `tracker.mcp_tools.comment`; cc anyone who must act via a one-line ADF
   mention comment.

### Labels — the ticket vocabulary (Jira environments)

| Axis | Values | Notes |
|---|---|---|
| Program / domain — stack any that apply | the tracker's existing program / product / version labels (read the label list; the env's set is tabled in `.context/reference/environment.md`) | Match the existing stacked convention, e.g. `["<program>","<product>","<product>_v1"]`. |
| Work-type — exactly one | the environment's work-type set, tabled in `.context/reference/environment.md` § Labels; `labels.shared` in the env config lists the ones a PR carries too | spell the shared ones identically to the PR labels. Where `environment.md` has no § Labels yet: use `labels.shared` plus the tracker's existing labels (read the label list), ask the user once which one is the work-type, and offer to table the answer. |

Unknown label strings are created silently — don't invent new ones; extend the environment's table by agreement.

## Adapter — GitHub issues (tracker.kind = github)

Tools: `gh` with the token per skill `gh-cli` (`github.sandbox_token_prefix`), in one of `tracker.repos`; links
`tracker.url_template` with `{repo}` + `{key}` → `[#162](https://github.com/<owner>/<repo>/issues/162)`
in terminal replies, bare `#162` (auto-linked) inside GitHub.

1. **Create with labels + milestone — they replace the sprint field.**
   `gh issue create -R <repo> --title … --body-file <file> --label <type>,<area> [--milestone <open milestone>]`.
   Labels come from the repo's **own** set (`gh label list -R <repo>`) — one type label + any area
   labels, spelled like the PR labels; don't invent new ones. Milestone = the open one covering today
   if the repo uses milestones (`gh api repos/<repo>/milestones`), else none.
2. **Parent instead of epic link.** Put `Parent: #<n>` in the body **and** add a task-list line
   `- [ ] #<new>` to the parent/tracking issue (`gh issue edit <n> -R <repo> --body-file …`, after
   reading its current body). Related issues: a `Related: #a, #b` line.
3. **Opening comment** = the Goal/Plan/Links block — put it in the issue body itself (the body is the
   opening comment on GitHub); `gh issue comment` only when the body was written by someone else.
   Mention whoever must act with `@login` on one line.

## Not in scope
Posting updates → `ticket-update`. Closing/transitioning → `ticket-close`. Opening the PR that
delivers the ticket → `pr-open` (which also moves the ticket to In Review where the tracker has that
state). Flushing the context doc → `session-handoff`.
