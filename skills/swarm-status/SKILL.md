---
name: swarm-status
description: "Posts the swarm digest: shipped, blocked, decisions open, spend, stalls, next, plus the operator lines, once per swarm.digest_hours and at each wave end. Use when the swarm tick reaches its digest step."
metadata:
  version: "1"
  updated: "2026-11-05"
  reviewed: "2026-11-05"
argument-hint: "<epic key> [--wave-end]"
---

# swarm-status — the digest of a running swarm

`$ARGUMENTS`: the epic key, and `--wave-end` when a wave just finished. `S=$BATON/skills/swarm-status/scripts`.
Run by the orchestrator at `swarm-run`'s digest step, in the main session. `render` and `due` only read the ledger.

## Steps

1. `python3 $S/digest.py due <key> [--wave-end]` → `due`, or `not due: next <iso>`. Not due ends the run, silently.
2. `python3 $S/digest.py render <key>` → the digest: a dated headline ending in `<!-- swarm-digest -->`, the six sections
   (Shipped, Blocked with who must act, Decisions open, Spend against the caps, Stalls, Next) and the closing
   `**For the operator**` lines (Can now / Do differently / Check with / Not covered). Post it as rendered; reword nothing
   and keep the marker, `swarm-inbox` drops any comment that holds it.
3. Post it as one delta on the tracking issue (skill `ticket-update`), then append the same text to the epic's context doc
   with `ctx_log`.
4. Where `systems.slack` is true, draft it to the owner's chat (skill `slack-draft`) and record the draft:
   `python3 $BATON/skills/swarm-inbox/scripts/inbox.py draft-add --key <key> --channel <c> --thread <t> --draft-id <id> --gist "swarm digest"`.
   With slack false this step is one line and nothing else: `chat tier not applicable here`.
5. `python3 $S/digest.py posted <key> < digest.txt` stores the time and text in the ledger directory; the next `due` and
   the mission-control swarm panel read it. Run it last, only after step 3 succeeded.

Headless (no `AskUserQuestion`): the open decisions of the digest also go into a `Decisions` block (`docs/carousel.md` § No
tool), the top option marked `(Recommended)`.

## Notes

- The digest is deterministic for one ledger and one clock: re-running `render` posts nothing and changes nothing.
- Spend with no reading is named under Not covered, never shown as zero.
- A failed post leaves `posted` unrun, so the next tick tries again.
