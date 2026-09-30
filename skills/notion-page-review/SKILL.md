---
name: notion-page-review
description: "Reviews a Notion page tree (page, sub-pages, every comment thread) against the user's position, walks each proposed comment (Comment, Update wording, Skip, batches of 4), posts only approved ones as thread replies or inline comments. Invoke when the user asks to \"go through\", \"catch up on\" or \"comment on\" a Notion page."
compatibility: "Designed for Claude Code; needs notion (systems.*)"
metadata:
  version: "13"
  updated: "2026-09-30"
  reviewed: "2026-09-28"
  requires: "notion"
  facts: "systems.slack"
user-invocable: true
---

# notion-page-review — read the whole tree, propose per section, approve one by one, post

The value is in three things: (1) nothing on the page or in its threads is missed, (2) the user approves
every single comment's wording before it goes out, (3) comments land where people are notified
(inside the thread they started), not as a wall on the page title.

> On a machine where `notion` is false, print `notion-page-review: not applicable here — notion is false` and stop.

## 1. Ingest the tree with discussions

1. `notion-fetch` the root page with `include_discussions: true`; list every child `<mention-page>` /
   sub-page and fetch each one the same way. Pages with no `discussion-urls` spans have no threads yet.
2. `notion-get-comments` on every page with `include_all_blocks: true` → the thread map:
   `discussion://<page>/<block>/<discussion>` → who said what, when. **Open threads only** — Notion's
   API has no way to retrieve a resolved discussion and no flag to ask for one, so a thread already
   resolved before this fetch is invisible to it, not merely hidden.
3. Record per page: sections, anchors (exact phrases), and every thread with its full id. Keep these
   ids in a scratch file (`$(python3 $BATON/context-db/bin/kit_profile.py scratch)/<page>-threads.md`) — you will need them after a compaction.
4. Pull the user's side from `.context/` (INDEX.md → the initiative's context doc, the last 1:1 / team
   meeting notes, the merged plan). Do **not** propose from memory of the page; propose from the fetch.

## 2. Build the proposal list

One row per proposed comment, numbered once and never renumbered (the user refers to "#13" later):

`# · PAGE → section → anchor phrase or thread owner · reply-to-thread | inline · proposed text (≤ 6 sentences)`

Rules for the proposals:
- **Reply into an existing thread whenever one exists on that section** — the people in it get
  notified. New inline comments only on rows nobody has touched.
- One idea per comment. Team-meeting rulings may be cited ("today's session", "<colleague> on <date>"); **private
  1:1 agreements are not claimed** — leave those "open for discussion" unless the user says otherwise.
- Questions for one expert are **not** a Notion comment. Before building the list, check the resolved-profile
  block already in your SessionStart context (`systems on: …` — `slack` present means true); once a
  compaction drops that block, or it never printed, fall back to
  `python3 $BATON/context-db/bin/kit_profile.py get systems.slack` once — never infer Slack from loaded MCP
  tools. Where it is true they become a Slack draft to that person (`slack-draft`) with the page link + the
  exact section to comment on; otherwise they go in the overview below as *redirected* — recipient,
  section, question — for the user to route themselves, and into the § 5 flush. The rule: a question
  for one person is a message to that person, never a comment on the page.
- Group the deliverable first as an overview (aligned / to discuss / to clarify), then start the loop.

## 3. The approval loop

`AskUserQuestion`, **4 items per call**, in page order (shape: `docs/carousel.md`):

- `header`: `"<n> · <topic>"` (≤ 12 chars; revisions become `"<n> v2 · <topic>"`).
- `question`: `PAGE → section → anchor` line, blank line, `Proposed text:` + the exact text in quotes,
  blank line, `Post this?`. For inline items name the anchor phrase; for replies name the thread owner.
- options, always these three, in this order: **Comment (Recommended)** · **Update wording** (the user types
  the change via Other) · **Skip**.
- Any free-text answer = a wording change or a redirect. Rewrite, then **re-present it as `vN` in the
  next batch** — never post a rewrite unseen. A redirect ("send this to X instead") leaves the loop.
- Post approved items **immediately after each batch**, in the same turn as the next AskUserQuestion,
  so a compaction never loses approved-but-unposted work. Log `#n → comment id` in the scratch file.

## 4. Posting

- Reply: `notion-create-comment` with `page_id` + `discussion_id` (full `discussion://…` url).
- Inline: `notion-create-comment` with `page_id` + `selection_with_ellipsis: "start…end"`. The snippet
  must be **unique on the page** — a short shared tail that also occurs elsewhere (e.g. a repeated
  parenthetical) fails with `Multiple occurrences found`; re-anchor to a distinctive phrase, never
  retry verbatim.
- Backticked words in the page are code spans: anchor around them, not through them.
- Never attach several comments to one block: several separate comments on the same title block all
  land in **one** discussion, where they are easy to miss and easy to leave unanswered. One block,
  one comment.

## 5. Flush (same session)

- Meeting/context doc (through the ctx tools): a "Notion comments posted" section — per item: `#n page/section → comment id`,
  skipped items with the reason, redirected items (recipient + the Slack draft id where `systems.slack`).
- Update the thread-id map in the context doc if the page will be revisited. The hooks re-index.

## Gotchas seen

- `notion-fetch` of a page **without** `include_discussions` hides the threads entirely; always pass it.
- Roughly one proposal in five needs a rewrite before it posts — plan for it and keep batches at 4 so
  a rewrite lands one batch later.
- Board/link "private" claims on a page: verify sharing through the linked tool's own permission
  listing (its connector, where this session has one) before
  telling anyone you will "open" something — it may already be shared.
