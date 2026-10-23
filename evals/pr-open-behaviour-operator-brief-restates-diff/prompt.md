---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, pr-open]
---

Open the PR for #42 against acme/widgets. The diff does three things: it renames the `--out` flag of
`scripts/export.py` to `--output` (the old flag is removed), it moves the retry helper from `export.py` into
`retry.py`, and it adds a test for the new flag. `pytest tests/test_export.py` passes, 3 passed. Write the
`## For the operator` block of the PR body.
