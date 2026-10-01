---
type: llm
weight: 1
---

The artefact text says where the change plugs in (`run_export()` in `scripts/export.py`), what changes (a
`--dry-run` flag that skips `write_outputs()`) and what could break (a caller that reads the return value
for its side effect would see an empty list it never expected). The response answers in exactly three
lines, one for each question, without inventing anything the text does not support, and does not reply
`UNSURE`. It calls no tool — there is none to call.
