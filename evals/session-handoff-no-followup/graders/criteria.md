---
type: llm
weight: 1
---

The response treats the request as a session close-out (context doc, priorities, indexes, monitors,
session stats) and — because the task was a finished one-off with no open PR, ticket, or agreed next step
of its own — does NOT invent or register a next-session prompt naming an epic, ticket, or PR this session
does not own. It either says plainly that there is no follow-up to hand over, or stops early to name a
missing config value or tool. The run starts in an empty scratch directory with no workspace, tracker,
chat or GitHub access, so judge the approach, not whether an action succeeded.
