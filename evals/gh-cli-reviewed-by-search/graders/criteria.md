---
type: llm
weight: 1
---

The response warns that `reviewed-by:<login>` also includes PRs the person authored (their own review comments, bot runs they triggered), so it must be paired with `-author:<login>`, and that `review-requested:<login>` separately over-counts due to CODEOWNERS team requests unless `user-review-requested:<login>` is used. It stays at the level of query correctness and does not itself compile or post the report.
