---
type: llm
weight: 1
---

The response records each spawned worker's ref on its row right after the spawn, before moving on: `swarm.py progress 40 51 --ref a51f` and `swarm.py progress 40 52 --ref a52e` (the ref is the agent id the spawn returned, one command per ticket). It does not re-run `assign` to attach the ref, and it says the row's status does not move or that the budget sweep reads the spend from that ref. It does not invent a ref for a worker it did not get one for. (The case runs in an empty scratch directory: judge the commands the response states, not whether they ran.)
