---
type: llm
weight: 1
---

`change.diff` sits next to this prompt and moves `ingest()` from calling `worker.process_record()` directly
to publishing onto a `records.process` queue, which a new `worker.handle_message()` consumes. PASS: the
response's data-flow diagram (or its plan for one) reflects that shape — `ingest` to queue/publish to the
new consumer, not a direct call — using the actual names (`ingest`, `publish`, `records.process`,
`handle_message`) or an unmistakable paraphrase of them, and also works through, or lists as what it will
do, at least one more of: a PR body text section, labels, reviewers, a commit-style check of the title, a
tracker link, watching the PR, a review request drafted rather than sent. It may still note it has no
workspace, tracker, chat or GitHub access for the rest. FAIL: the response never reads or mentions
`change.diff`'s actual content, draws a generic or invented data flow that does not match the diff, or
stops solely to name `change.diff` (or any other file/tool) as missing — a stop that names a missing file
proves nothing about the skill when the file was there to read.
