# Contributing

Thanks for helping. Whatever merges to `main` reaches every machine on its next sync, so each change goes
through an issue and a reviewed PR.

**The short version**

- **Open an issue first.** Describe what, why, and which environments it affects, and give it one `area:*` label.
  If you find something else while you work, open a new issue instead of growing the PR.
- **Title the PR as a Conventional Commit**, for example `feat(pr-open): add the diagram plan`. The title becomes the
  squash commit.
- **Start the PR body with `Closes #N`**, or with `Refs #N` for one step of a larger issue.
- **Run `make -C context-db ci` in your worktree** before you push. It runs every gate CI runs; it needs `origin/main`
  fetched, and a missing tool (shellcheck, the `claude` CLI) fails it unless you pass `ALLOW_SKIP=1`.
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
make -C context-db ci                                 # every CI gate (ALLOW_SKIP=1 where a tool is missing)
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

**Placeholders only.** The kit reads the same on every machine, so an example never carries a real value:

| Example of | Write | CI enforces |
|---|---|---|
| ticket key | `KEY-123`, `KEY-456`, `ABC-123` (a GitHub issue: `#42`) | any other number behind `KEY-`/`ABC-`, any other key shape |
| slug or file name | `<slug>`, `key-123-<slug>` | a number other than 123/456 in `key-<n>-…`; the slug's words are the reviewer's |
| repository, org, host | `<owner>/<repo>`, `acme/widgets`, `acme.example.com` | org hosts, `<org>/<repo>` paths from your env store |
| person, team, employer | `<user>`, `<team>` | nothing: the reviewer's leak-by-meaning lens, and your own `leaks.markers` |
| date | a kit release date | nothing: a dated workplace anecdote is a leak by meaning |

`make -C context-db review-gate-tree` runs the same shapes over the whole tree before you push.

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
