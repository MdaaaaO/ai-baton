---
type: llm
weight: 1
---

`change.diff` sits next to this prompt and adds retry-with-backoff to `WebhookClient.send()` in
`webhook_client.py` (a `max_retries`/`backoff_base` pair and an exponential sleep loop around the existing
`requests.post`/`raise_for_status`). PASS: the response's PR body (or its plan for one) names that specific
change — retries, backoff, `WebhookClient`/`send()` — not a generic placeholder, and also works through, or
lists as what it will do, at least one more of: labels, reviewers, a commit-style check of the title, a
tracker link, watching the PR, a review request drafted rather than sent. It may still note it has no
workspace, tracker, chat or GitHub access for the rest. FAIL: the response never reads or mentions
`change.diff`'s actual content, is only generic git/PR advice that would fit any change, or stops solely to
name `change.diff` (or any other file/tool) as missing — a stop that names a missing file proves nothing
about the skill when the file was there to read.
