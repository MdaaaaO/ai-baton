---
type: llm
weight: 1
---

The response treats the request as writing or restructuring README.md (and/or CONTRIBUTING.md) in house style — badges, a short pitch, code within the first screen, tables over prose, detail moved to docs/ — and mentions or runs the checker (readme-check.py) to verify the result. It does not write docs/ reference pages from scratch as the primary deliverable and it does not edit a CHANGELOG. The run starts in an empty scratch directory with no live repo access, so judge the approach, not whether a file actually changed.
