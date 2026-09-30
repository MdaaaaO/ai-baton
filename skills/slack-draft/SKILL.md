---
name: slack-draft
description: "Lifecycle of every Slack message a session drafts for the user: create it as a draft (never paste text), record it, and READ the channel or thread before any follow-up, recreate or status claim, because the user edits drafts before sending. Invoke on every `slack_send_message_draft`, before calling a draft \"pending\", and when resuming one."
compatibility: "Designed for Claude Code; needs slack (systems.*)"
metadata:
  version: "7"
  updated: "2026-09-28"
  reviewed: "2026-09-24"
  requires: "slack"
---

# slack-draft — a draft is a hand-off, not a send

Owner decision (2026-09-18): "drafts sometimes go out and you should confirm if they went out and in what
form." A session that assumes a draft is still pending — or gone — without reading the target
channel gets the state wrong in both directions: it re-drafts a message that is already live (a
duplicate in the composer the user must discard) or reports "unsent" for something the team already
answered.

> On a machine where `slack` is false, print `slack-draft: not applicable here — slack is false` and stop.

## 1. Create

- `slack_send_message_draft` into the target channel / DM / thread (`thread_ts` for replies).
  Never paste draft text in the terminal (WORKSPACE.md § Rules). Formatting: `reference/formatting.md`
  beside this file (`**bold**`, `<url|label>`, ≤55-char code-block lines).
- **One attached draft per channel.** If the tool returns `draft_already_exists`, that pending
  draft is the one to update — do not create a second. A thread draft counts against the same
  channel slot.
- Tell the user in one line where it is. **Thread drafts hide in the thread's reply box and under
  "Drafts & Sent", not in the channel composer** — say so when the draft is a thread reply.

## 2. Record — immediately

Write into the context doc's Session log (and the session file's pending list):
channel id + name, `thread_ts` if any, the `draft_id`, and a one-line gist of what it says.
The gist is what §3 diffs against once it goes out.

## 3. Before you touch it again — read first

Run this **before** any of: re-creating the draft, updating it, reporting its status, drafting a
follow-up to the same people, or acting on "no reply yet".

1. `slack_read_thread` (thread draft) or `slack_read_channel` (top-level) on the target. Read the
   full thread (omit `oldest=`; the parameter is unreliable) and filter by timestamp locally to
   find messages after the draft's creation time. Look for a message **from the user** whose substance
   matches the gist.
2. Outcomes:
   - **Sent** → record *sent* + timestamp + **what they changed** (dropped/added points, wording
     shifts). Their edits are signal: they tell you which of your positions they did not want stated,
     so downstream messages and context docs must follow the sent version, not your draft.
     Then read the replies (threads included) before any follow-up.
   - **Still pending** (no matching message; recreate attempt returns `draft_already_exists`) →
     leave it; do not nudge.
   - **Gone, not sent** (no matching message and recreate succeeds) → only now is "the draft
     vanished" a true statement; say so and record the new `draft_id`.
3. Never infer "sent" or "vanished" from the draft tool alone — the tool cannot list or delete
   drafts, and a successful re-create proves only that no draft is attached *now*.

## 4. Duplicates

If you did create a duplicate (step 3 skipped), say so at once: the tool has no delete, so the user
must discard it themselves in the thread reply box / "Drafts & Sent". Don't leave it for them to find.

## 5. On resume / after compaction

A summary's "draft pending" is a claim to re-verify per §3, not a fact. Do it before the first
status line you give the user about that message.

Related: `pr-open` § 6 (review-request drafts). One draft per channel (§1) — it may still vanish (sent or
discarded) without telling you, so verify the surface, not the tool result; a timed-out tracker write needs
the same discipline (it usually still landed).
