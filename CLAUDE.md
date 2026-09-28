# ai-baton — the kit

This directory is the kit: skills, agents, hooks and the `context-db/` engine that every machine pulls. The
always-on rules for a working session are `WORKSPACE.md` (imported by the workspace root `CLAUDE.md`), not this file.

Changing a kit file: `CONTRIBUTING.md` (issue → branch → PR, versioning, the skill contract). Reviewing one:
`docs/REVIEW.md`. Before a PR, from this checkout: `make -C context-db ci`.
