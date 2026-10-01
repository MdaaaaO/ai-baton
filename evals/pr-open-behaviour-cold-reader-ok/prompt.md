---
max_turns: 6
timeout_seconds: 180
allowed_tools: []
tags: [behaviour, cold-reader]
---

Read the following artefact text. Answer in exactly three lines, one each: WHERE it plugs in, WHAT
changes, WHAT could break. If the text does not say, reply with exactly one line instead: UNSURE
<question> naming what is missing. No other prose — you have no tools and nothing else to read.

## Summary

Adds a `--dry-run` flag to `scripts/export.py`. When set, `run_export()` builds the same file list and
prints it instead of calling `write_outputs()`, which it skips entirely. The flag defaults to false, so
every existing caller of `scripts/export.py` behaves exactly as before. The one risk: a caller that reads
`run_export()`'s return value for its side effect (a cron script checking the files actually written)
would now see an empty list it never expected.
