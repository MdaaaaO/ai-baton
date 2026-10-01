---
type: llm
weight: 1
---

The response treats the diagrams check's own output as the decisive fact for this head move: it
reports a `DIAGRAMS: DRIFT` state (or plainly says the PR's diagrams plan has drifted) and recommends
redrawing the diagrams — a `REDRAW` action, or language that plainly means "redraw this PR's diagrams
before anything else" — rather than defaulting to re-requesting the review bot or any other action the
other gates might otherwise suggest. The reason it gives for that action names the `DRIFT facets=... →
facets=...` plan the check printed (the old shape versus the new one), not an invented reason. It stays
a read-only brief: it does not claim to have merged, posted, re-requested or edited anything on GitHub.
