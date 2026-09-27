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
to `main` regardless. GitHub offers no branch protection on a free-plan private repo, so
`.github/workflows/main-guard.yml` is the server-side backstop: on every push to `main` it checks the
commit against a squash-merged PR and, when the two don't match, opens (or comments on) a tracking issue
— it cannot block the push, only flag it after the fact. Then, with `.claude/` on `main` and clean,
it fetches and fast-forwards. It refuses — `.sync-status` says `error …` and it pulls nothing — when
`.claude/` is on another branch, has uncommitted changes, or carries local commits on `main`: each
is work that belongs on a branch + PR, and the message says how to move it there. It never
resets, stashes or discards anything.

It takes a lock so two sessions never run the pull at once, never fails the caller, logs to
`sync.log` (ignored, trimmed to its last 200 lines; the `main at <sha>` line only when `HEAD` moved) and
leaves the outcome in `.sync-status` — which `sync-check.sh` reads at every `session-register`, so a
refused pull is seen by the next session instead of staying silent:

| `.sync-status` | means | `sync-check` warns |
|---|---|---|
| `pending <epoch>` | written just before the fetch (bounded by `timeout 60`, or a shell watchdog where `timeout` is missing); still there = the run was killed | when older than 5 minutes |
| `ok <kit@sha>` | fetched; fast-forwarded or already in step | never |
| `offline <epoch> since <ts>` | the fetch could not resolve or reach origin; the epoch is the first run of the streak | after 3 days |
| `error <reason>` | off `main`, dirty, ahead, fetch failed or timed out, fast-forward failed | always |

A run that finds the lock busy logs `skipped` and leaves `.sync-status` alone: the holder writes the newer
state. The thresholds are constants at the top of `sync.sh` and `sync-check.sh`.

The `SessionEnd` hook in `settings.json` runs it in the background whenever a session ends
(`sh "$CLAUDE_PROJECT_DIR/.claude/sync.sh"`, `async: true`) — for the kit that is a pull, nothing more.
What Claude Code does with that hook, per the [hooks reference](https://code.claude.com/docs/en/hooks)
(§ Run hooks in the background, § SessionEnd): an `async` command hook is spawned and Claude Code
"continues immediately"; "the `timeout` field is not enforced on async hooks", so the hook's `timeout: 120`
is only a reminder and `sync.sh` bounds its own fetch. On a normal exit Claude Code "waits up to 5 seconds
for all async hooks to finish", then exits and "the hook process is orphaned"; on a forced exit (`Ctrl-C`) it
"exits immediately without waiting". Output of an async hook goes to the debug log only. A sync that is
killed on the way out (the terminal closing with it) is what a stale `pending` reports.

Nothing here commits: kit changes are branch + PR (`CONTRIBUTING.md`). Signing is a host-only
concern for the repos under `github.signed_commits`, handled by the `sign-queue` skill.
