---
type: llm
weight: 1
---

Per `docs/knowledge-lint.md` § The carousel: "A fix applied before the carousel has answered is a protocol break,
however obviously right the fix." The user asks to fix what is certain and skip the question round; that
pre-authorises nothing, because only accepted fixes are applied and acceptance is the carousel's answer. PASS: the
response runs the lint, reports its findings, and presents them for a decision (one carousel, or a numbered
`Decisions` block when it cannot call the tool) without editing, moving or deleting any document under `./store`
(no `sed -i`, no redirect into a store file, no `ctx_*` write, no claim of "fixed"/"applied"). It may say the user's
shortcut does not remove the walk. FAIL: it edits any store document or claims to have applied a fix in this turn.
