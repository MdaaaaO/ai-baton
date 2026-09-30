---
type: llm
weight: 1
---

The response mines the given repo/URL for candidates (rather than asking the user to supply a ready
list), drafts each one, dedups against open issues, then walks the candidates with the user one at a
time before anything is created — matching the explicit ask to check for duplicates and walk before
creating.
