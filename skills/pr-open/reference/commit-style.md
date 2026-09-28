# Commit style — Conventional Commits by default, the repo may override

Owner decision, 2026-09-25: "by default we want to follow conventional commits if the repo doesn't override it"
(core rule, `WORKSPACE.md` § Rules → Workflow & scope; spec `$BATON/docs/commit-style.md`). The resolver is
`python3 $BATON/context-db/bin/commit_style.py` — repo marker (`<repo>/.claude/commit-style`: `conventional` |
`ticket-key` | `free`, or a commitlint config → conventional) → env config `commits.repos.<owner/repo>` →
`commits.default` → `conventional`. Never decide the style by reading the repo's recent log: a repo that drifted
is not a repo that overrode.

- **Conventional**: `<type>(<scope>)!: <description>` — types `feat fix docs chore refactor test ci build perf
  style revert`, lowercase scope (the component: `dbt`, `dag`, `<service>`, `kit`), `!` for a breaking change,
  lowercase imperative description, ≤ 72 chars, no trailing period. **The tracker key goes inside the
  description** (`feat(dbt): KEY-123 add the fact table`), never as the prefix — the key is text in the title
  (§ Links) and a link in the body.
- **PR title = the squash-commit subject.** Under squash merging the title becomes the commit on `main`, so it
  passes the same check (`commit_style.py title`). The type maps to the type label (step 3): `feat` →
  `enhancement`, `fix` → `bug`, `docs` → `documentation`; other types take no type label unless the repo has one.
- **Gates**: the kit repo's `hooks/commit-msg` (installed with `pre-push` via `core.hooksPath`), `sign-queue`'s
  `enqueue.sh` (refuses a message file that fails the check), and step 0 in `SKILL.md` for direct commits. Bypass
  only deliberately (`KIT_SKIP_COMMIT_STYLE=1` / `SIGN_QUEUE_SKIP_STYLE=1`) and say so.
- **Overrides are the repo's call, not the session's**: a repo that wants `ticket-key` (`KEY-123: description`)
  or `free` says so in its own marker file or in the env config's `commits.repos` (set by the user as one object — `kb.py
  config-set commits '{"default":"conventional","repos":{"<owner/repo>":"ticket-key"}}'` — a dotted key
  would break on a repo name with a dot). Never add a marker to someone else's repo.
