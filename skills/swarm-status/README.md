# swarm-status

The swarm's digest: one dated post per `swarm.digest_hours` (default 4) and at each wave end, rendered from the epic's
ledger by `scripts/digest.py`, so the same ledger and clock give the same text.

| Section | Reads |
|---|---|
| Shipped | tickets done or merged, with their PR |
| Blocked | blocked or parked tickets and who must act |
| Decisions open | the ledger's open decisions and the ticket each holds |
| Spend | the budget assessment: `row.spent`, `led.spent`, `row.spend` against the caps |
| Stalls | claimed tickets past their stall window |
| Next | what the tick does next (assign, integrate, wait) |

It closes with the four operator lines (`docs/operator-brief.md`). The headline ends with `<!-- swarm-digest -->` so
`swarm-inbox` never reads the digest back as a human comment.

**Commands:** `digest.py render <key> [--now] [--json]`, `due <key> [--wave-end]`, `posted <key>` (text on stdin, writes
`state/swarm/<key>/digest.json`, which the mission-control swarm panel shows).

**Posts to:** the tracking issue (`ticket-update`), the epic's context doc, and chat via `slack-draft` where
`systems.slack` is true.
