---
type: llm
weight: 1
---

The response runs, or describes running, the kit-health audit: the report sections (kit frontmatter, leaks, env-store config, machine wiring, engine smoke), producing a report and stamping the environment's health file, then walking any findings with the user. It does not treat this as installing or scaffolding the kit for the first time. Judge the approach, not whether a report file was actually written, since the run starts in an empty scratch directory.
