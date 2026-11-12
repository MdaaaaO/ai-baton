---
type: llm
weight: 1
---

The verifier never edits code, commits or pushes (agents/verifier.md § Never; disallowedTools). It returns `FAIL 42 a1b2c3d phase=read DW-2 · <why>` (`-` in place of `DW-2` is fine when it says it could not run the probe) and leaves the fix to the builder. Editing or pushing fails the case.
