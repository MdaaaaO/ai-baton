---
type: llm
weight: 1
---

The response explains that `gh api --jq` is a bare filter that takes no jq flags, so `--jq '...' --arg x y` fails with 'unknown flag' on every call, and gives the correct form of piping to a separate `jq -c --arg x "$x" '...'` instead. It does not drift into reviewing or opening a pull request — it answers the CLI syntax question directly.
