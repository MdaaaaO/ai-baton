---
type: llm
weight: 1
---

The PR body carries a `## For the operator` section with exactly four lines, labelled `Can now:`, `Do differently:`,
`Check with:` and `Not covered:`, each of 20 words or fewer, written for the person who runs the export script. The
lines name actions and effects: a command to run (`--output`), the step that breaks (scripts that still pass
`--out`), a command that shows it works (the `pytest` run, or running the script), and a gap or `Nothing.`. Flag any
response whose lines restate the diff instead — "Renamed `--out` to `--output`", "Moved the retry helper into
`retry.py`", "Added a test" — with no action for the operator, or that omits a label, leaves a line empty or `n/a`
instead of `Nothing.`, or runs over 20 words in a line. That restating block is the near miss this case guards
against: it is short and true, and it helps nobody who runs the script tomorrow.
