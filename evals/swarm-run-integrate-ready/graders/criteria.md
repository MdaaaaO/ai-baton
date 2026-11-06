---
type: llm
weight: 1
---

The response integrates each `READY`: a draft PR from the worker's body file, the review overview, code-owner coverage, the verifier's read of that exact head, and a merge only through the gate pinned to the `READY` sha. It keeps each PR a draft until the overview, the miss check and the verifier's read are back. (The Skill call itself is `fired.md`'s job; grade the outcome here. A case runs in an empty scratch directory with no ledger or tracker, so judge the approach, not whether a call succeeded.)
