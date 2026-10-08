# Context sync — the `.context/` store over git

`.context/` is per-person content, never part of the kit. When you work from more than one machine (a laptop, a
cloud session, a second clone), the store can be **its own private git repository**, and the kit keeps it in step
with the remote on its own: every context write commits and pushes; every session start pulls. The engine for this is
`context-db/bin/ctx_sync.py`; the switch is the env config key `context.sync` (#515).

## What it does

| When | Pass | Where you see it |
|---|---|---|
| after every write under `.context/` (a ctx tool or a `Write`/`Edit` under `sessions/`) | `commit → fetch → rebase → push` from the asynchronous `PostToolUse` hook, after the catalogs were regenerated | nothing when it worked; the scratch dir's `hooks.log` holds the line; a conflict is shown by the next synchronous hook as a system message |
| `session-register`, `session-touch`, `session-end`, `session-rename` (and `heartbeat.sh`'s touches) | the same pass, synchronously | the command prints `context sync: pushed …`, `push pending …` or `conflict: …` |
| session start (`startup`, `resume`, `clear`) | `commit → fetch → rebase`, no push (`run --no-push --quiet`) | `context sync: pulled …` in the hook output when something came in, nothing when the store was already current; a pending conflict leads the registry brief (with or without ctx installed) |
| by hand | `make -C $BATON/context-db context-sync` / `context-sync-status` | the one line; exit 3 on a conflict |

Each pass runs under the store's own lock (`.git/ctx-sync.lock`), so two sessions on one machine never race git's
index; a pass that finds the lock held leaves the change to the holder's `git add -A` or to the next write. Network
calls carry a short timeout and `GIT_TERMINAL_PROMPT=0`: no network, no credential, or a slow remote gives
`committed …, push pending` and the next pass retries — a hook never hangs on it. A pass the hook's own timeout
kills mid-rebase leaves the store with a rebase in progress; the next pass sees that nobody holds the lock and
the status says `running`, aborts that rebase and starts over (an abort git itself refuses is recorded as a
conflict — `could not undo an interrupted rebase` — for you to clean up). A rebase **you** started by hand is left alone:
the pass stops with exit 3 and the hooks say `a rebase is in progress in the store` until you finish or abort it.

## The switch — `context.sync`

`kb.py config-set context.sync <mode>`; `kb.py migrate` adds the key with `auto` to an older store.

| Mode | Behaviour |
|---|---|
| `auto` (default) | the store is its **own** git work tree (`git rev-parse --show-toplevel` is the content root) with an `origin` remote → commit, pull --rebase, push. Its own work tree without a remote → commit only. Not its own work tree (a store inside a bigger repository, or no `.git` at all) → nothing: that repository's owner decides when to commit |
| `off` | never touch git |
| `commit` | commit, never push (a store you push by hand, or a machine without the credential) |
| `push` | like `auto`; a detached HEAD or a missing remote still degrades to commit only |

`make -C $BATON/context-db context-sync-status` prints the effective mode and why (`push origin/main; 0 ahead, 0
behind`, `off (the store is not its own git repository)`, …); `kit-health` § 4 carries the same line and warns when
a git store runs with the sync off, when commits wait for a push, and errors on a pending conflict.

## Setting it up

1. Make the store a repository with a **private** remote:
   ```sh
   cd .context && git init -b main && git add -A && git commit -m "context: initial import" \
     && git remote add origin <private remote url> && git push -u origin main
   ```
   The store already carries a `.gitignore` for lock files and caches; the first pass adds the managed block of
   `.gitattributes` below. Nothing else is needed — `context.sync` is `auto`.
2. On every other machine, clone the same remote **as** `.context/` (where the kit expects the content root:
   `docs/layout.md` § Content root), then run the kit's `setup.sh` as usual — it adopts the clone as a ctx store
   without rewriting the docs.
3. Cloud or sandboxed sessions need the private repository attached (read **and** push access) and cloned to the
   content root before the session's first write, the same way as any other repository they work on.

Keep the identity the commits carry in mind: a repository with no `user.name`/`user.email` of its own commits as the
registered session name with `<github login>@users.noreply.github.com` (from `WORKSPACE_GITHUB_LOGIN`), so GitHub's
email-privacy push rejection never bites. Commit subjects are `context: <doc paths> (<session>)` — never doc content.

## Many sessions, one store — how conflicts are merged

Two sessions on two machines that write the same minute both commit locally; the second one to push rebases onto
the first. Most of what both sides touch is append-only or generated, so the kit tells git how to merge it instead
of stopping (`.gitattributes`, the managed block `ctx_sync.py` writes; the driver commands are repo-local config,
rewritten on every pass because a plugin install's path changes per release):

| Path | Driver | Rule |
|---|---|---|
| `*.md` (every doc) | `ctx-doc` | **frontmatter** key by key: the side that changed a key wins; both changed → `updated` the newer date, any other key the side whose `updated` is newer. **Body**: `git merge-file --union` — both sides' lines are kept, nothing is dropped (a `## Session log` gets both entries; a `## Remaining work` both sides rewrote gets both versions, one after the other, for a person to tidy) |
| `sessions/_ledger.md`, `.audit/*.jsonl` | `union` | append-only: both sides' rows |
| `.audit/seq` | `ctx-max` | the larger counter |
| `INDEX.md`, `SESSION_INDEX.md`, `sessions/archive/INDEX.md` | `ctx-regen` | generated: one side is kept, the catalogs are regenerated and committed after the rebase |

After a rebase that merged docs, the pass runs `ctx validate --changed --adopt` over them. A doc the union left
invalid (duplicate frontmatter, a broken heading — rare, the frontmatter is repaired before the body is written)
**undoes the rebase** (`reset --hard ORIG_HEAD`; your local commit stays), records the finding in
`.git/ctx-sync/status` and exits 3. The same happens when git itself cannot apply a commit (a doc deleted on one
side and edited on the other, a renamed file). Nothing is lost in either case: your commit is on your branch, the
remote is untouched, and the hooks say so after the next write and at the next session start:

```
context sync: conflict pending in <domain>/<doc>.md — ctx validate: …; resolve in the store, then `make -C $BATON/context-db context-sync`
```

Resolving one is ordinary git in `.context/` (the pass already aborted its own rebase, so the work tree is clean
and your commits are on the branch): `git pull --rebase origin <branch>`, fix the files git names (or the doc the
validate finding names), `git rebase --continue`, then `make -C $BATON/context-db context-sync` until it prints
`pushed` or `clean`.
A doc the union merged twice over reads fine to ctx (`validate` passes), so it never blocks the sync — the
duplicated section is yours to tidy in the next flush; `session-handoff` step 5 names it.

## What it never does

- It never rewrites a doc outside a merge, never force-pushes, never deletes a branch, never touches a repository
  that is not the store's own.
- It never runs `ctx maintain`'s own git commit (`CTX_GIT`); the two are independent and both are safe — a store
  that had `CTX_GIT=1` just gets fewer, larger commits from ctx and the pushes from the kit.
- It never pushes on its own from a store without `origin` or with a detached HEAD, and never sets up a remote.
- It never puts doc content in a commit subject, a hook message or a log line — only paths.

## Files

| Path | Role |
|---|---|
| `context-db/bin/ctx_sync.py` | the engine: `run`, `status`, the three merge drivers; stdlib only |
| `context-db/bin/ctx_adapter.py` | wires the pass into `post-tool-use-async` and the conflict line into `post-tool-use` / `brief-registry` |
| `context-db/bin/session.py` | runs the pass after every registry change and prints its line |
| `hooks/hooks.json`, `settings.json` | the SessionStart pull (`run --no-push --quiet`) and the ctx-tool `post-tool-use` entry |
| `<store>/.gitattributes` | the managed merge-driver block (tracked, so every clone merges alike) |
| `<git dir>/ctx-sync/status`, `…/merged`, `<git dir>/ctx-sync.lock` | the pass's state: the pending conflict, the paths a driver merged, the per-store lock — in `<store>/.git`, or in the directory a `.git` gitfile (`--separate-git-dir`, a worktree) names |
| `skills/kit-health/kit-health.py` § 4 | the health row |
| `context-db/tests/test_ctx_sync.py` | two clones against a bare remote: push, pull, concurrent appends, frontmatter repair, the validate gate, the lock, every mode |
