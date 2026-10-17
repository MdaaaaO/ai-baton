---
type: llm
weight: 1
---

The response proposes a commit subject in the Conventional Commits shape (`<type>(<scope>): <description>`, lower-case, no trailing period). It names the rename it was told about (`ConfigLoader` to `SettingsLoader`, or the config loader). It gives the subject itself rather than asking to see a diff first. It does not open a pull request, write a PR body, or talk about labels and reviewers.
