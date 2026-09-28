---
type: llm
weight: 1
---

The response treats this as a kit-health audit of the machine's install: running the health check script (or describing that it will), then walking sections like versioning frontmatter, env-value leaks, env-store coverage/staleness, wiring (CLAUDE.md imports, memory symlink, systems) and an engine smoke, and walking any finding with the user (Fix, Ticket, Accept). It does not treat this as a first-time scaffold of the workspace. Judge the approach, since the run starts in an empty scratch directory with no live kit install to actually audit.
