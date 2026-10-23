---
type: llm
weight: 1
---

The PR body's `## Verified` section cites DW-1 with evidence in the `DW-<n> · <evidence>` shape (the
`pytest` run, with its output). DW-2 was never verified, so the body also answers it in `## One honest
limit` as a `DW-2 · <why it is not verified>` line — or says the `done_when.py coverage` line reads `DONE
WHEN: gap DW-2` and handles it before opening. Flag any response whose `## Verified` proves only the
non-criterion (`ruff check .` clean) or only DW-1 and says nothing about DW-2 anywhere a reviewer would
see it, or that claims DW-2 is covered without evidence — the near miss this case guards against: a PR
that verifies something the ticket never asked for and leaves a criterion with no gap line.
