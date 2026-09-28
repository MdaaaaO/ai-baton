---
type: llm
weight: 1
---

The response treats this as reading the real CI findings (via the failed job's logs or the reviewdog PR comments) and applying the matching fix for each rule code, rather than guessing at sqlfluff's output. It does not skip straight to guessed fixes without reading the actual findings first.
