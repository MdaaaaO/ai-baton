---
type: llm
weight: 1
---

The response treats this as (part of) a kit-health audit: it checks env-store coverage and staleness and scans kit files for leaked environment values, then reports findings and, for a leak, offers to add an anchored allow-list entry or fix it. It does not treat this as scaffolding a new workspace or as a ticket/PR action.
