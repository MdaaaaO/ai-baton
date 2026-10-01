---
type: llm
weight: 1
---

`ticket-update`'s Pivot grammar is a delta, not a restatement: `**Was** — … / **Now** — … / **Why** —
…`, and *Why* cites the recorded decision (the on-call lead's call) rather than re-deriving it. The
user's last line asks for a full recap of the Goal, Plan and history — that is the near miss this case
watches for. Flag any response whose Pivot comment re-describes the whole ticket (restates the original
Goal/Plan, or summarises everything that happened since opening) instead of writing the tight Was/Now/Why
delta. A response that holds the line — a short delta citing the decision, declining to pad it with a
full recap — passes.
