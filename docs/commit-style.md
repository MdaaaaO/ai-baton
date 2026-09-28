# Commit style — Conventional Commits by default, the repo may override

Owner decision, 2026-09-25: *"by default we want to follow conventional commits if the repo doesn't
override it."* This is an always-on rule (`WORKSPACE.md` § Rules → Workflow & scope). It applies to
every commit a session writes in any repo and to every PR title (the squash-commit subject).

## Resolution — `context-db/bin/commit_style.py`

```
python3 $BATON/context-db/bin/commit_style.py resolve [--dir <repo-dir>] [--repo <owner/repo>]
python3 $BATON/context-db/bin/commit_style.py check   [--dir …] <msg-file | ->        # exit 0 ok / 1 style / 2 config or I/O error
python3 $BATON/context-db/bin/commit_style.py title   [--dir …] "<PR title>"          # same, one line
python3 $BATON/context-db/bin/commit_style.py label   "<subject>"                     # type → GitHub label
```

First hit wins:

1. **The repo's own marker** — `<repo>/.claude/commit-style`, one word: `conventional` | `ticket-key` |
   `free`. A commitlint config in the repo root (`commitlint.config.*`, `.commitlintrc*`, a `commitlint`
   key in `package.json`) or a conventional-release config (`.conventional-release.toml`, a
   `[tool.conventional-release]` table in `pyproject.toml`) also means `conventional`.
2. **The env config** — `commits.repos["<owner/repo>"]` (repo derived from `origin`). Set only when the
   repo's owner asked for it, as one object (a dotted key breaks on a repo name with a dot):
   `kb.py config-set commits '{"default":"conventional","repos":{"<owner/repo>":"ticket-key"}}'`.
3. **The env config default** — `commits.default` (`kb.py init --blank` writes `conventional`). The whole
   `commits` key is optional: `kit_verify.py` validates it when present, absent means the kit default.
4. **The kit default** — `conventional`.

A repo's recent log is **not** a source: a repo that drifted has not overridden. Never add a marker
file to someone else's repo — propose it to the owner if the style there really is something else.

## Styles

| style | subject | notes |
|---|---|---|
| `conventional` | `<type>(<scope>)!: <description>` | types `feat fix docs chore refactor test ci build perf style revert`; scope lowercase `[a-z0-9._/-]` (the component: `dbt`, `dag`, `<service>`, `kit`); `!` = breaking; description lowercase imperative, ≤ 72 chars total, no trailing period; **tracker key inside the description** — `feat(dbt): KEY-123 add the fact table`, never `KEY-123: …` |
| `ticket-key` | `<KEY>: <description>` | key per the env config's `tracker.key_regex`; ≤ 72 chars, no trailing period |
| `free` | anything non-empty | for repos that explicitly want no rule |

Always accepted: `Merge …`, `Revert "…"`, `fixup! …`, `squash! …` (git writes them). A message's
second line must be blank; every line that starts with the comment char (`#`, or the repo's `core.commentChar`) is
ignored, exactly as git's `cleanup=strip` removes it — so a `#123 …` subject needs `core.commentChar` set to another char.

Type → GitHub type label (`pr-open` step 3): `feat` → `enhancement`, `fix` → `bug`, `docs` →
`documentation`; the other types take no type label unless the repo names one (`labels.repos`).

## Gates — where the check runs

| where | how | deliberate bypass |
|---|---|---|
| the kit repo (`.claude/`) | `hooks/commit-msg`, installed with `hooks/pre-push` through `core.hooksPath` by `setup.sh` / `sync.sh` | `KIT_SKIP_COMMIT_STYLE=1 git commit …` |
| signed-commit repos | `skills/sign-queue/enqueue.sh` runs `check --dir <worktree> <msg-file>` and refuses the job | `SIGN_QUEUE_SKIP_STYLE=1` |
| any direct commit / PR title | `pr-open` step 0: `check` the message file, `title` the PR title, before `git commit` / `gh pr create` | none — fix the subject |
| another repo that wants the hook | `git -C <repo> config core.hooksPath <abs>/.claude/hooks` (the hook exits 0 when the kit is not next to it) | as the kit |

Say so in the terminal line whenever a bypass was used, and why.

## PR titles and merge strategy

Under **squash merging** the PR title becomes the commit subject on `main`, so the title follows the
style exactly like a commit (`commit_style.py title`). Kit repo recommendation: squash-merge every PR
and keep "default to PR title" on, so `main` reads as Conventional Commits even when a branch carries
fixups. (Repo settings are the owner's call; the kit does not change them.)

## Config shape

```json
"commits": { "default": "conventional", "repos": { "<owner/repo>": "ticket-key | free | conventional" } }
```

The key is optional (absent = the kit default, § 3 above); `kit_verify.py` validates every value when it is present; `kit-health` § 3 reports the store.
