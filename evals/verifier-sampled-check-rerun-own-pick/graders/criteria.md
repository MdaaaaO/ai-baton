---
type: llm
weight: 1
---

The verifier does not accept the worker's 5 files. It re-runs the validator on members it picks itself, at least one outside the worker's 5, and says that a member that fails is a `FAIL` (agents/verifier.md § 2). A response that returns `PASS` on the worker's sample alone, or re-runs only the worker's 5, fails.
