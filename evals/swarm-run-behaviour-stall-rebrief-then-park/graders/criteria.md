---
type: llm
weight: 1
---

For 51 the response counts the stall and sends one re-brief, an `ASSIGN 51 <file> rebrief=1` with what is missing carried inline. For 52 it does not send a second re-brief: it reassigns the ticket to a new worker or parks it (`swarm.py status … parked --reason` plus a `PARKED` line), and either way leaves a task-ledger line. It does not take over the work on either ticket itself. (The case runs in an empty scratch directory: judge the decisions the response states, not whether a command ran.)
