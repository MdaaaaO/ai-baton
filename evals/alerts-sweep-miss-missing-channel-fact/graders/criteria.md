---
type: llm
weight: 1
---

The response treats this as filling in a missing env fact: running the relevant discovery tool, verifying the candidate, and writing it back with provenance — env-init's job. It does not run the alerts-sweep read/classify pass, which only consumes that fact once set and returns NEEDS when it is missing.
