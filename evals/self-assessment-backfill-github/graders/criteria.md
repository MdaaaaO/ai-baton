---
type: llm
weight: 1
---

The response recognizes the zero-`.context/`-hits case, switches to back-fill mode, and composes the week's self-assessment from GitHub and the tracker as the systems of record. It does not treat this as a live-session recap or skip straight to asking the user what they did. The run starts in an empty scratch directory with no live workspace, tracker, chat or git access, so judge the approach, not whether an external call succeeded.
