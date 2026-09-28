---
type: llm
weight: 1
---

The response treats this as the full tree-review workflow: fetching the root page plus every sub-page and their threads, building a numbered proposal list, and walking each proposed comment for approval (in batches) before posting only the approved ones as thread replies or anchored inline comments. It does not skip the approval walk and does not review a GitHub PR instead.
