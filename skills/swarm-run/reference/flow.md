# flow.md — one tick as a picture

The order is `docs/swarm.md` § The swarm tick. The three shaded stops wait for the owner; none of them stops the tick:
the work behind each one waits, and everything else goes on.

```mermaid
flowchart TD
  R[1 Read: swarm.py tick, tracker, git] --> I[2 Inbox: /swarm-inbox, its MARK/SEEN/CLOSE-DRAFT trailers, apply, ack; STOP and HOLD first]
  I --> D[3 Decisions: BLOCKED, NEEDS, DECISION]
  D --> A[4 Assign: assignable only, one message, one worktree each]
  A --> S[5 Stalls: assigned and claimed rows]
  S --> B[6 Budget: caps, hard stop]
  B --> G[7 Integrate: draft PR, owners, review, ready, merge gate]
  G --> V[8 Verify: phase=read on a READY head, phase=merged, phase=gates]
  V --> DG[9 Digest]
  DG --> F[10 Flush: render, heartbeat, next wake-up]
  D -.-> HD{{owner: an open decision}}
  B -.-> HB{{owner: a budget cap}}
  G -.-> HM{{owner: a protected-path merge}}
  classDef human fill:#ffe08a,stroke:#b8860b
  class HD,HB,HM human
```

Why this order: the inbox comes first because a `STOP` or a `HOLD` changes what may be assigned; decisions come
before assignment because an answer can change the plan; assignment reads the epic cap itself, and the budget step
then reads what this tick's returns and spawns added, so a breach stops the next spawn and never a merge already due;
the digest comes last so it reports this tick.
