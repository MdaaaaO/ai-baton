---
type: llm
weight: 1
---

The response treats this as an ordinary git problem — explaining the rejected push and how to reconcile it (fetch/rebase or merge, then push) — not as a `gh` CLI (GitHub API) usage question. It does not bring up `gh api` pagination, `--jq`, search rate limits or auth traps, since none of those apply to a plain `git push` conflict.
