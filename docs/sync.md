# Sync — how the kit moves between machines

The kit's `main` only ever fast-forwards to `origin/main`; every change arrives as a merged PR.

> **Plugin install:** this page is about a clone (`.claude/`, a git checkout). A plugin install has no `sync.sh`, no
> SessionEnd sync hook and no git hooks; it moves release by release through `claude plugin update`
> (`docs/packaging.md` § Updating). Updating one safely, step by step:
> [`plugin-setup.md`](plugin-setup.md#updating-a-plugin-install-safely) § Updating a plugin install safely.

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

The worktree comes off your kit checkout — `.claude` here, a plain clone of the repo on a plugin install
([`CONTRIBUTING.md`](../CONTRIBUTING.md#development-setup) § Development setup has both):

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
to `main` it checks the commit against a squash-merged PR, retrying a few times a few seconds apart before
it decides there's no match, and when the two still don't match, opens (or comments on) a tracking issue
— it cannot block the push, only flag it after the fact. Then, with `.claude/` on `main` and clean,
it fetches and moves `.claude/` to the right target (below). It refuses — `.sync-status` says `error …`
and it pulls nothing — when `.claude/` is on another branch, has uncommitted changes, or carries local
commits on `main`: each is work that belongs on a branch + PR, and the message says how to move it there.
It never resets, stashes or discards anything.

It takes a lock so two sessions never run the pull at once, exits 0 unless that lock is busy, logs to
`sync.log` (ignored, trimmed to its last 200 lines; the `main at <sha>` line only when `HEAD` moved) and
leaves the outcome in `.sync-status` — which `sync-check.sh` reads at every `session-register`, so a
refused pull is seen by the next session instead of staying silent:

| `.sync-status` | means | `sync-check` warns |
|---|---|---|
| `pending <epoch>` | written just before the fetch (bounded by `timeout 60`, or a shell watchdog where `timeout` is missing); still there = the run was killed | when older than 5 minutes |
| `ok <kit@sha>` | fetched; fast-forwarded, already in step, or already past the held tag | never |
| `ok <kit@sha> unverified (<reason>)` | `--accept` applied a release tag whose manifest attestation could not be checked (no `gh` or one too old, not authenticated, a manifest.txt that could not be downloaded for any reason, or the verify call itself got no answer) | not from `sync-check.sh` — the standalone word `unverified` and the reason are in `.sync-status` and in `make claude_sync`'s output for this run only; the durable mark lives in `.sync-unverified` (see below), which `kit-health` § 1 reads and warns on, naming the command to verify by hand |
| `held <tag>` | a newer release tag is waiting in `.sync-preview`; `make claude_sync` applies it | not from the status line — its kit check names the waiting tag for as long as `HEAD` lacks it (unless `.sync-rejected` already names this tag — see below) |
| `offline <epoch> since <ts>` | the fetch could not resolve or reach origin; the epoch is the first run of the streak | after 3 days |
| `error <reason>` | off `main`, dirty, ahead, fetch failed or timed out, fast-forward failed, an accepted release tag failed manifest verification, or a previously rejected tag is still waiting | always |

## Channel: release tags or `main`

By default `.claude/` follows the highest `v[0-9]*` release tag that `origin/main` contains, not
`origin/main` itself — a clone install never runs code an unattended `SessionEnd` sync pulled straight
off `main`. Before the first release tag exists it tracks `main` directly, same as the opt-out below.

A newer release tag is never applied by a bare run. Instead `sync.sh` writes the ignored
`.sync-preview` (the commits between `HEAD` and the tag, the changed `skills/`, `agents/` and `hooks/`,
and the other files a clone runs unattended — git hooks, `settings.json`, `setup.sh`, `sync.sh` itself,
`context-db/bin/`) and leaves `.sync-status` at `held <tag>`. Only `sh .claude/sync.sh --accept` (what
`make claude_sync` passes) fast-forwards to the held tag; the `SessionEnd` hook never passes `--accept`,
so an unattended run can only hold, never apply.

Before that fast-forward, `--accept` verifies the held tag against its attested manifest the way
[`CONTRIBUTING.md`](../CONTRIBUTING.md) § Releases documents: download the tag's `manifest.txt` release
asset, `gh attestation verify` it against `.github/workflows/release.yml` (owner/repo read from the
checkout's own `origin` remote, never hardcoded), then check the manifest's `commit <sha>` line against
the tag's own commit. Verified → applies as above. The check ran and **failed** — `gh attestation verify`
rejected the manifest (any failure of that call other than "no answer from GitHub", below — an HTTP 404
included) or the manifest names a different commit than the tag — → applies nothing, `.sync-status` is
`error <reason>` and the run exits 1 with the reason on stderr — the one error that does, since `--accept`
only ever runs in the foreground. The tag and reason are also written to the ignored `.sync-rejected`
(one line: `<tag> <reason>`); see below for what that changes about the next unattended run.

The check **cannot run at all** — no `gh` on `PATH`, a `gh` from before `gh attestation` existed, `gh`
not authenticated, an `origin` that is not on github.com (an ssh host alias such as
`git@github.com-work:owner/repo`, which looks like a github.com remote but is not one, counts — it is
left unparsed rather than guessed at), a `manifest.txt` that could not be downloaded for **any** reason
at all (no asset, network, timeout, anything else — a release that predates the manifest and carries no
`manifest.txt` asset included), or the verify call itself got no answer from GitHub (network, timeout, a
rate limit, a server error, bad credentials — gh's own rejection of the manifest is never read as this,
even when its wording overlaps) — → applies anyway: a clone must still be able to catch up with no
network or an unauthenticated `gh`, so `.sync-status` is `ok …` with the standalone word `unverified` and
a short reason appended. The word and the reason live in `.sync-status` and in `make claude_sync`'s
output; `sync-check.sh` does not warn on it, `kit-health` § 1 does. `kit.channel main` never verifies — there is no release tag
to verify against.

[`SECURITY.md`](../SECURITY.md#verifying-a-release) § Verifying a release has what the manifest and the
attestation check prove, and what immutable releases and the tag ruleset on `v*` do and don't guarantee.

**A rejected tag stays rejected.** `.sync-rejected` (ignored, same shape as `.sync-status`'s detail: one
line, `<tag> <reason>`) remembers the tag the check last **failed** — not one it could merely not check —
so that the next unattended run (no `--accept`) does not overwrite the rejection with `held <tag>` as if
nothing had happened: when the tag `sync.sh` would otherwise hold is the one named in `.sync-rejected`, it
reports `error` again instead, with the same reason. When a later run replaced that status line (an
`offline` or `pending` run), `sync-check.sh` names the rejected tag itself instead of calling it waiting.
A later release tag than the rejected one is held as usual (and the stale `.sync-rejected` is cleared).
The file is removed once a release applies, verified or unverified. Re-running `sh .claude/sync.sh --accept` on the rejected tag verifies it again from scratch —
once the release is fixed (a new manifest, a corrected tag), the check can pass and the release goes
through.

**An unverified apply stays marked, past the run that applied it.** `.sync-unverified` (ignored, same
shape as `.sync-rejected`: one line, `<commit-sha> <reason>`) remembers the commit a release was applied
to without the manifest check being able to run. Unlike `.sync-status`, a later plain run does not
overwrite it: `kit-health` § 1 reads this file, not `.sync-status`, so the warning survives past the one
session that applied the release. It is cleared once `main` no longer points at the commit it names — a
later fast-forward, verified or not, or anything else that moved the branch — or once a later
`sh .claude/sync.sh --accept` on that same, still-current commit gets a verified answer (`gh` now
installed or authenticated, say); a check that still cannot run refreshes the stored reason and reports
`ok … unverified (<reason>)` again, and one that runs and fails reports `error` (exit 1) and rewrites the
mark as `<commit-sha> rejected (<reason>)` — the release stays applied, nothing is rolled back, and
`kit-health` § 1 reports an error with that reason from then on (the status line goes back to `ok` at the
next plain run; the mark does not). A `rejected` mark is never turned back into `unverified`: a later
`--accept` whose check cannot run leaves it as it is, and only a verified answer or a moved `main`
removes it. The mark follows `main`, not `HEAD`: a run made while the checkout is
detached or on another branch refuses to sync and leaves the mark alone.

A contributor who wants the old behaviour — always track `origin/main`, no hold — opts out with:

```
git -C .claude config kit.channel main
```

That config lives in the clone's own `.git/config` (never committed): it is a per-machine choice, not a
kit setting. `make claude_sync` applies the held tag the same way on the release channel; on `kit.channel
main` the flag is a no-op (there is nothing to hold).

A run that finds the lock busy logs `skipped` with the holder (`held by pid N since T`), says so on stderr,
exits 3 and leaves `.sync-status` alone: the holder writes the newer state. The lock is `flock` on
`.sync.lock` where it exists, else an atomic `mkdir` of `.sync.lock.d/`; the holder writes its pid, the
time it took the lock, and its own process start time (`ps -o lstart=`) into it. A `.sync.lock.d/` is
stale and taken over at once when its owner pid is gone (`kill -0`) — or when the pid is alive but `ps
-o lstart=` for it no longer matches the start time recorded at lock-take time, i.e. the pid was reused
(same number, a different process) after a reboot or pid wraparound. There is no time ceiling on a live
owner: a lock dir without an owner file at all (from before owner files existed) is stale only after 10
minutes, but a live owner — recognised as still being the same process — is never aged out, however
long it holds the lock. An owner line with no recorded start time (written by a sync.sh from before this
check) or one `ps` cannot answer falls back to `kill -0` alone, same as before. The take-over runs under
a second `mkdir` lock (`.sync.lock.break/`) and re-checks there, so two runs that saw the same dead owner
cannot both take the lock. The thresholds are constants at the top of `sync.sh` and `sync-check.sh`.

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
concern for the repos under `systems.signed_commits`, handled by the `sign-queue` skill.
