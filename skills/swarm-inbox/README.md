# swarm-inbox

A forked, haiku-priced sweep that turns every channel of a running swarm into one list of normalized items for the
orchestrator's tick, or `NO-OP`. It never writes; the main session runs the `SEEN` and `CLOSE-DRAFT` trailers it returns.

**Reads:** worker outboxes, the orchestrator's inbox, peer messages, human comments on the epic and its tickets, PR
events, due watches and, where `systems.slack` is true, the chat threads of pending drafts.

**Item:** `ITEM <kind> <scope> src=<source> ref=<ref> at=<utc> "<gist>"`, the gist one line of 120 characters at most.
Kinds: `line` (a swarm line, verbatim), `delta` (a human comment that adds scope: it waits for a go), `decision` (a human
question: it goes to the digest's Decisions), `comment`, `pr-event`, `watch`, `chat`, and `draft-sent`, `draft-edited`,
`draft-pending` (a chat draft read back as sent, sent with edits, or still pending). Bot comments are dropped.

**Needs:** nothing for the core. The chat tier needs `systems.slack`.

**Without it:** where `systems.slack` is false the chat step prints `swarm-inbox: chat tier not applicable here — slack
is false` and the other tiers run as usual.

**Example:**

> **claude (fork):** `ITEM delta 674 src=comment ref=c91 at=2026-11-05T14:02:11Z "also cover the retry path"`
> then `SEEN — run: python3 $BATON/skills/swarm-inbox/scripts/inbox.py seen 674 --at 2026-11-05T14:02:11Z`
