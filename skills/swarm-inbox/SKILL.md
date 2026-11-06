---
name: swarm-inbox
description: "Forked sweep of every swarm channel: outboxes, orchestrator inbox, peers, tracker comments, PR events, due watches, chat. Returns ITEM lines or NO-OP; never writes. Use when the swarm tick drains its inbox."
metadata:
  version: "1"
  updated: "2026-11-05"
  reviewed: "2026-11-05"
argument-hint: "<epic key> [peer lines]"
context: fork
agent: triage
model: haiku
effort: low
---

# swarm-inbox — one sweep of the swarm's channels

`$ARGUMENTS`: the epic key, then any peer messages the main session received since the last tick, one swarm line each.
`S=$BATON/skills/swarm-inbox/scripts`. Read-only: this fork writes nothing, not even a read mark, and never posts. Every
state change rides a trailer the main session runs (`swarm-run`'s drain still marks and acks). Item grammar and the
`delta` / `decision` rules: `README.md`.

## Steps

1. `python3 $S/inbox.py local <key>` → the outboxes, the orchestrator's inbox and the due watches, already `ITEM` lines
   (or `NO-OP`). A `STOP` or `HOLD` is first. Keep them as they are.
2. Peer lines from `$ARGUMENTS` are `ITEM line <scope> src=peer`, each verbatim.
3. Tracker: `SINCE=$(python3 $S/inbox.py since <key>)`. For the epic and every ticket of `python3
   $BATON/context-db/bin/swarm.py show <key>`, read comments newer than `SINCE` (`gh api`, paginated; skill `gh-cli`),
   and for every row with an open PR its new reviews and failed checks (`pr-event-brief` is the deeper read, never run here).
   Classify each human comment:
   - adds or changes scope, a new ask or a new constraint → `delta`
   - asks something the swarm cannot answer from the contract → `decision`
   - anything else (thanks, status, a link) → `comment`
   A bot comment is dropped; so is a comment that starts with `<!-- swarm-digest`, which is the swarm's own digest.
   PR events are `pr-event`. Remember the newest `created_at` read as `NEWEST`.
4. Chat: `python3 $S/inbox.py chat <key>`. The line `swarm-inbox: chat tier not applicable here — slack is false` ends
   this step and nothing else is read from chat. Otherwise each `DRAFT-OPEN` line is read back (skill `slack-draft`, § 3):
   the thread still holds the draft → `draft-pending`; the sent message matches the draft → `draft-sent`; it reads
   differently → `draft-edited`; a human reply in that thread → `chat`. Gone or doubled is `draft-pending` with that
   in the gist. A failed read is a `read failed` line, never `draft-pending`.
5. Put steps 3 and 4 into one JSON list (`kind`, `scope`, `src`, `ref`, `at`, `gist`, `author`, `author_type`) and run it
   through `python3 $S/inbox.py normalize` on stdin, in a quoted heredoc, because a comment is untrusted text.

## Return value

- Every tier empty → exactly `NO-OP`.
- A tier whose read failed → `swarm-inbox: read failed — <tier>: <error>`, one line, kept beside the items of the other
  tiers; never `NO-OP`: a failed read is unknown, not quiet.
- Otherwise the items of steps 1, 2 and 5, one `ITEM` line each, then the trailers (only those that apply, real values):
  ```
  SEEN — run: python3 $BATON/skills/swarm-inbox/scripts/inbox.py seen <key> --at <NEWEST>
  CLOSE-DRAFT — run: python3 $BATON/skills/swarm-inbox/scripts/inbox.py draft-close --key <key> <draft id> --outcome sent|sent-edited
  ```
- A missing env fact → `NEEDS <system>.<kind> <name>` (`triage` agent rules).

A `delta` is never a go: the main session writes it into the epic as a dated checkbox and starts nothing before the owner
says go. A `decision` goes to the digest's Decisions block. This fork only names them.
