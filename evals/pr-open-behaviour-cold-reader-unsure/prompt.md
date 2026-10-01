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

Touches `scripts/export.py`, `tests/test_export.py` and `docs/export.md`. Adds a couple of new tests and
tidies the docs wording.
