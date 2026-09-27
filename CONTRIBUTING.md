# Contributing

Thanks for helping. Whatever merges to `main` reaches every machine on its next sync, so each change goes
through an issue and a reviewed PR.

**The short version**

- **Open an issue first.** Describe what, why, and which environments it affects, and give it one `area:*` label.
  If you find something else while you work, open a new issue instead of growing the PR.
- **Title the PR as a Conventional Commit**, for example `feat(pr-open): add the diagram plan`. The title becomes the
  squash commit.
- **Start the PR body with `Closes #N`**, or with `Refs #N` for one step of a larger issue.
- **Run `make -C .claude/context-db ci`** before you push. It is the same job CI runs, and it needs no setup.
- **Bump a skill whose behaviour changes.** Raise `metadata.version` and add one line to the top of
  [`docs/CHANGELOG.md`](docs/CHANGELOG.md).
- **Keep machine values out of the kit.** No ids, hosts, org names or people. They belong in the env store.

## Use of AI

Most of this kit is written with Claude Code, and Claude reviews every PR. You can use any tool you like. One rule
applies: **you must understand what you submit, whoever or whatever wrote it.**

## Development setup

```sh
git -C .claude worktree add ../.worktrees/kit_<topic> -b <topic> origin/main
cd .worktrees/kit_<topic>
make -C context-db ci                                 # validator, tests, compile and link checks
make -C context-db verify-skill UNIT=skills/<name>    # one skill only
```

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
