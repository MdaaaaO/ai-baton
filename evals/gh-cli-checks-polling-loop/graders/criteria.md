---
type: llm
weight: 1
---

The response flags the specific trap: `gh pr checks --json` fails with 'unknown flag' on older gh (e.g. the apt package), and if that failure is swallowed behind `2>/dev/null` the loop can spin forever reading it as 'still pending'; it recommends the portable REST-based wait script instead of hand-rolling a sleep loop. It does not itself review or merge the PR. Judge the guidance, not whether a live check actually resolved, since the run starts in an empty scratch directory with no live GitHub access.
