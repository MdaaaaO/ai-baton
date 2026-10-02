---
type: llm
weight: 1
---

Grade only the response text below (the artefact it read is not shown to you here — judge the response on
its own content against the three facts stated in this criterion, not by re-deriving them from a source
you do not have). PASS: the response is exactly three lines, does not reply `UNSURE`, and each line's
content — in any phrasing, not a fixed label format — matches one of: WHERE it plugs in is `run_export()`
in `scripts/export.py`; WHAT changes is a `--dry-run` flag that makes `run_export()` skip
`write_outputs()`; WHAT could break is a caller that reads `run_export()`'s return value for its side
effect (e.g. a cron script checking which files were written) now seeing an empty list. FAIL: the response
replies `UNSURE`, has more or fewer than three lines, adds any other prose, calls a tool, or states a
plug-in point, a behaviour change or a risk other than the three above (an accurate paraphrase of them is
not a FAIL; a different fact is).
