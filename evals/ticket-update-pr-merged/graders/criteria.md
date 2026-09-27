---
type: llm
weight: 1
---

The response treats the request as a progress update on a tracker ticket: it drafts or posts ONE short, dated comment that carries only the new facts, in a delta shape (a status headline and only the sections that apply, such as Win / Pivot / Next / Verified), and does not restate the ticket's history. The run starts in an empty scratch directory with no workspace, tracker, chat or GitHub access, so judge the approach, not whether an action succeeded: stopping early to name a missing config value or tool is fine.
