# Sync — how the kit moves between machines

The kit's `main` only ever fast-forwards to `origin/main`; every change arrives as a merged PR.

> **Plugin install:** this page is about a clone (`.claude/`, a git checkout). A plugin install has no `sync.sh`, no
> SessionEnd sync hook and no git hooks; it moves release by release through `claude plugin update`
> (`docs/packaging.md` § Updating).

## Commands

```
make claude_sync          # from the workspace root, if your Makefile has the target
sh .claude/sync.sh        # equivalent
```

## The change flow

**The kit's `main` is PR-only.** Nothing in `.claude/` is committed or pushed by any script: `.claude/`
stays on `main` and only ever moves by fast-forward to `origin/main`. A kit change travels
issue → branch → PR → merge on GitHub → `make claude_sync` on each clone, run by that machine itself or by its
`SessionEnd` hook (the full model — issue per PR,
checks, Claude review, labels — is [`CONTRIBUTING.md`](../CONTRIBUTING.md)):

```
git -C .claude worktree add ../.worktrees/kit_<topic> -b <topic> origin/main
# edit there, commit (unsigned is fine; subject = Conventional Commits, hooks/commit-msg enforces it), then
git -C ../.worktrees/kit_<topic> push -u origin <topic>       # branch pushes pass the hook
# open the PR (pr-open skill: labels, diagram plan, watch); merge on GitHub; then on every clone:
make claude_sync          # a plugin install: claude plugin update, once the change is released
```

## What `sync.sh` does

`sync.sh` first installs the hooks (`git config core.hooksPath .claude/hooks`; `hooks/commit-msg` checks
every kit commit's subject against `docs/commit-style.md`; `hooks/pre-push`
refuses every push that would update `refs/heads/main`, including `push origin HEAD:main`, forced
pushes and deletes). That guard is local, so it has real bypasses: `KIT_ALLOW_MAIN_PUSH=1` is the
deliberate one-off override, `git push --no-verify` skips it outright, a fresh clone has no guard until
`setup.sh`/`sync.sh` installs `core.hooksPath`, and any other machine or the GitHub UI can push straight
to `main` regardless. On GitHub the repo's `main` ruleset (PR required, no force-push or delete) stops
most of those, but repo admins bypass it — so the hook stays as defence in depth that refuses before a push
ever leaves the machine, and `.github/workflows/main-guard.yml` is the server-side backstop: on every push
to `main` it checks the commit against a squash-merged PR and, when the two don't match, opens (or comments on) a tracking issue
— it cannot block the push, only flag it after the fact. Then, with `.claude/` on `main` and clean,
it fetches and fast-forwards. It refuses — `.sync-status` says `error …` and it pulls nothing — when
`.claude/` is on another branch, has uncommitted changes, or carries local commits on `main`: each
is work that belongs on a branch + PR, and the message says how to move it there. It never
resets, stashes or discards anything.

It takes a lock so two sessions never run the pull at once, never fails the caller, logs to
`sync.log` (ignored) and leaves the outcome in `.sync-status` — which `sync-check.sh` reads at every
`session-register`, so a refused pull is seen by the next session instead of staying silent. The `SessionEnd` hook in `settings.json` runs it in the background whenever a
session ends (`sh "$CLAUDE_PROJECT_DIR/.claude/sync.sh"`) — for the kit that is a pull, nothing more.

Nothing here commits: kit changes are branch + PR (`CONTRIBUTING.md`). Signing is a host-only
concern for the repos under `github.signed_commits`, handled by the `sign-queue` skill.
