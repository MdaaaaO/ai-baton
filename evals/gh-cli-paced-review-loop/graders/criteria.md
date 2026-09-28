---
type: llm
weight: 1
---

The response calls out the traps that apply to a large `gh api` loop: pacing/backgrounding a loop this size, checking the command's exit status separately from its output rather than swallowing errors, using `--paginate` on `/reviews` so page 2 isn't invisible, and writing progress to a file rather than the transcript. It does not perform the actual PR review or open a PR — it stays at the level of how to run the loop safely. Judge the guidance given, not whether calls actually ran, since the run starts in an empty scratch directory with no live GitHub access.
