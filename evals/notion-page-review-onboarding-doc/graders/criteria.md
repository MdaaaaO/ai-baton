---
type: llm
weight: 1
---

The response treats this as a full review of the Notion page tree: fetching the root page and every sub-page along with their comment threads, drafting proposed comments against the user's position, and walking each one for approval (Comment, Update wording, Skip) before posting only approved ones into the right thread. It does not draft a brand-new Notion page from scratch and does not review a GitHub pull request. Judge the approach, since the run starts in an empty scratch directory with no live Notion access.
