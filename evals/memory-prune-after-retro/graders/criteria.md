---
type: llm
weight: 1
---

The response treats the request as an auto-memory prune: it runs, or says it will run, the read-only memory check (index bytes, long index lines, note/index mismatches, rule copies), classifies each note against what the always-on prefix, the skills and the context docs already load, archives every original before any change and asks once before rewriting notes and the index. It does not write a new memory, a session retro, a handoff or a cost report. The run starts in an empty scratch directory with no memory dir or workspace, so judge the approach: stopping with "no memory dir — nothing to prune" is fine.
