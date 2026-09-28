---
type: llm
weight: 1
---

The response commits and pushes directly (or describes doing so), since no signing rule applies here. It does not enqueue a sign-queue job, which would be pointless where `signed_commits` is false.
