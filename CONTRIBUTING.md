# Contributing

Thanks for helping. Whatever merges to `main` reaches every machine on its next sync, so each change goes
through an issue and a reviewed PR.

**The short version**

- **Open an issue first.** Describe what, why, and which environments it affects, and give it one `area:*` label.
  If you find something else while you work, open a new issue instead of growing the PR.
- **Title the PR as a Conventional Commit**, for example `feat(pr-open): add the diagram plan`. The title becomes the
  squash commit.
- **Start the PR body with `Closes #N`**, or with `Refs #N` for one step of a larger issue.
- **Run `make -C context-db ci` in your worktree** before you push. It is the same job CI runs, and it needs no setup.
- **Bump a skill whose behaviour changes.** Raise `metadata.version` and add one line to the top of
  [`docs/CHANGELOG.md`](docs/CHANGELOG.md).
- **Keep machine values out of the kit.** No ids, hosts, org names or people. They belong in the env store.

## Use of AI

Most of this kit is written with Claude Code, and Claude reviews every PR. You can use any tool you like. One rule
applies: **you must understand what you submit, whoever or whatever wrote it.**

## Development setup

Develop in a worktree of a kit checkout, never in the checkout itself and never under the plugin cache
(`$BATON` on a plugin install, overwritten on every update). Which checkout depends on how you installed the kit:

| install | the kit checkout | once |
|---|---|---|
| clone (`.claude/` is the kit) | `.claude` | nothing: `sync.sh` sets the git hooks |
| plugin | a plain clone beside your repos: `git clone https://github.com/MdaaaaO/ai-baton` | `git -C ai-baton config core.hooksPath hooks` (nothing else installs the hooks there) |

Then, from the workspace root, with `<checkout>` from the table:

```sh
git -C <checkout> worktree add ../.worktrees/kit_<topic> -b <topic> origin/main
cd .worktrees/kit_<topic>
make -C context-db ci                                 # validator, tests, compile and link checks
make -C context-db verify-skill UNIT=skills/<name>    # one skill only
```

A session runs the installed kit, not your branch, on either install. To try a branch in a session, start
Claude Code from the workspace root (project settings load from the start directory, not from the worktree) with
`claude --plugin-dir .worktrees/kit_<topic>`; the checkout stays on `main` either way.

The kit checkout itself stays on a clean `main`. `hooks/pre-push` refuses any push to `main`, and
`hooks/commit-msg` checks every commit subject. Tests use the standard library only and need no network.
Every script gets a `unittest` in `context-db/tests/`, and every fix gets a regression test.

## Pull requests

| Step | What happens |
|---|---|
| Checks | `kit-verify`, `pr-title` and `pr-issue` must be green |
| Review | Claude reviews when you open the PR and again on every push. It posts `Verdict: approve` or `request-changes` |
| Threads | Answer and resolve every review thread, either with a fix or with a `review-followup` issue |
| Merge | Squash only. `auto-merge` merges once checks, verdict and threads all pass. Otherwise the owner merges |
| Labels | One type label (`enhancement`, `bug`, `documentation`) and one `area:*` label |

A wording-only PR bumps nothing. Mark it with the `wording` label or put `[skip-bump]` in the title.

Writing a skill? Start from [`docs/authoring.md`](docs/authoring.md), the checklist, and copy the scaffold in
`docs/templates/skill/`.

## Releasing (maintainers)

`make kit_release_dry` shows the next version, and `make kit_release` opens the release PR. On a plugin install, run
them as `make -f $BATON/workspace.mk … KIT_CHECKOUT=<kit clone>`. Squash-merge it with
the title unchanged. The `release` workflow then tags it and publishes the notes.

## The full rules

[`docs/contributing.md`](docs/contributing.md) has the complete rules for the issue-to-merge flow, versioning,
the skill contract and frontmatter, secrets, releases, labels, CI runners, testing and code review.
[`docs/REVIEW.md`](docs/REVIEW.md) is the reviewer's rule book.
