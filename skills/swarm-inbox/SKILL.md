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
state change rides a trailer the main session runs. Item grammar and the
`delta` / `decision` rules: `README.md`.

## Steps

1. `python3 $S/inbox.py local <key>` → the outboxes, the orchestrator's inbox and the due watches, already `ITEM` lines
   (or `NO-OP`). A `STOP` or `HOLD` is first. Keep them as they are: a `line` item is the swarm line byte for byte, the
   quotes inside it included, and the tick applies it as it stands. A last `READ-TO …` line is what it read each box to:
   keep it for the `MARK` trailer, it is not an item.
2. Peer lines from `$ARGUMENTS` are `ITEM line <scope> src=peer`, each verbatim.
3. Tracker: `SINCE=$(python3 $S/inbox.py since <key>)`. For the epic and every ticket of `python3
   $BATON/context-db/bin/swarm.py show <key>`, read comments created at or after `SINCE` (`gh api`, paginated; skill `gh-cli`), skipping the ids `python3 $S/inbox.py
   since <key> --refs` prints (already taken at that very time),
   and for every row with an open PR its new reviews and failed checks (`pr-event-brief` is the deeper read, never run here).
   Classify each human comment:
   - adds or changes scope, a new ask or a new constraint → `delta`
   - asks something the swarm cannot answer from the contract → `decision`
   - anything else (thanks, status, a link) → `comment`
   A bot comment is dropped; so is a comment that holds `<!-- swarm-digest`, the swarm's own digest.
   PR events are `pr-event`. Remember the newest `created_at` read as `NEWEST` and the ids of the comments read at exactly
   that time as `NEWEST_IDS`.
4. Chat: `python3 $S/inbox.py chat <key>`. The line `swarm-inbox: chat tier not applicable here — slack is false` ends
   this step and nothing else is read from chat. Otherwise each `DRAFT-OPEN` line is read back (skill `slack-draft`, § 3):
   the thread still holds the draft → `draft-pending`; the sent message matches the draft → `draft-sent`; it reads
   differently → `draft-edited`; the draft is deleted from the thread → `draft-gone`; a human reply in that thread →
   `chat`. Doubled is `draft-pending` with that in the gist. A failed read is a `read failed` line, never `draft-pending`.
5. Put steps 3 and 4 into one JSON list (`kind`, `scope`, `src`, `ref`, `at`, `gist`, `author`, `author_type`) and run it
   through `python3 $S/inbox.py normalize` on stdin, in a quoted heredoc, because a comment is untrusted text.

## Return value

- Every tier empty → exactly `NO-OP`.
- A tier whose read failed → `swarm-inbox: read failed — <tier>: <error>`, one line, kept beside the items of the other
  tiers; never `NO-OP`: a failed read is unknown, not quiet.
- Otherwise the items of steps 1, 2 and 5, one `ITEM` line each, then the trailers (only those that apply, real values):
  ```
  MARK — run: python3 $BATON/skills/swarm-inbox/scripts/inbox.py mark <key> <the READ-TO pairs of step 1>
  SEEN — run: python3 $BATON/skills/swarm-inbox/scripts/inbox.py seen <key> --at <NEWEST> --refs <NEWEST_IDS, comma-separated>
  CLOSE-DRAFT — run: python3 $BATON/skills/swarm-inbox/scripts/inbox.py draft-close --key <key> <draft id> --outcome sent|sent-edited|gone
  ```
  `SEEN` is withheld whenever a tracker read failed, even in part: the next sweep reads the same comments again, and the
  ids in `--refs` keep the ones already taken from coming twice. `MARK` does not depend on the tracker.
- A missing env fact → `NEEDS <system>.<kind> <name>` (`triage` agent rules).

A `delta` is never a go: the main session writes it into the epic as a dated checkbox and starts nothing before the owner
says go. A `decision` goes to the digest's Decisions block. This fork only names them.
