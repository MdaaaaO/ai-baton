# Troubleshooting — silent hooks, kit-health RED findings, uninstalling

Every kit hook is written to never fail loudly: a broken hook must not break the session. That means a real
failure produces no error dialog and no non-zero exit — only a line in the transcript, or nothing at all. This
page says where to look, and how to remove the kit once you no longer want it.

## 1. A hook failed and printed nothing (or you're not sure)

### SessionStart (`hooks/hooks.json`)

Runs two things every session: `kit_profile.py session-env` (exports the plugin identity into
`$CLAUDE_ENV_FILE`) and `kit_profile.py workspace-rules` (prints `WORKSPACE.md` into the session on a plugin
install). The hook always `exit 0`s — Claude Code itself never sees it as failed — so the only signal is what it
prints and where its stderr went:

- **stderr** is appended to `<scratch>/hooks.log` when the scratch directory exists and is writable, else it is
  discarded to `/dev/null`. Find the scratch directory the same way any skill does:

  ```sh
  python3 $BATON/context-db/bin/kit_profile.py scratch   # prints the directory; hooks.log lives inside it
  ```

- **`session-env` failing** prints one line into the session itself: `ai-baton: session-env failed writing to
  $CLAUDE_ENV_FILE - see <path>/hooks.log`, or `- (no log available)` when the scratch directory could not be
  used. Look there first.
- **`workspace-rules` failing** prints `ai-baton: the SessionStart hook could not load WORKSPACE.md
  (kit_profile.py workspace-rules failed) - the always-on rules are missing from this session; run /kit-health`.
  Do that — § 4 of a kit-health run checks the exact wiring this hook depends on (below).

Nothing prints at all only when both commands succeed — a session with no `ai-baton:` line at start had a clean
SessionStart hook.

### SessionEnd (`settings.json`, clone only)

A plugin install has no SessionEnd hook (`docs/sync.md`'s banner). On a clone, the SessionEnd hook runs
`sync.sh` in the background with both streams thrown away and its own exit code ignored
(`… >/dev/null 2>&1 || true`) — by design, since an async hook's output is not visible anywhere a person would
see it. `sync.sh` does not rely on that output surfacing: it writes `.sync-status` (`ok <sha>` / `error <reason>`
/ `offline …` / `pending …`) and appends to `sync.log` on every run, and `sync-check.sh` reads `.sync-status` at
the next `session-register` so a refused pull is never silent for long. `docs/sync.md` § What `sync.sh` does has
the full state table; to see a failure immediately rather than at the next registration, run
`sh $BATON/sync.sh` yourself from the workspace root.

### ctx-store hooks (`PreToolUse` deny, `PostToolUse`, `SessionStart` briefs — both install paths)

`hooks/hooks.json` (plugin) and `settings.json` (clone) also run the ctx-store adapter,
`context-db/bin/ctx_adapter.py` (`docs/engine-cli.md`), which calls ctx-store's `ctx` through its verbs only:

| Event | Call |
|---|---|
| `PreToolUse` on `Write`/`Edit`/`MultiEdit`/`NotebookEdit` of a `*.md` doc under an adopted content root | deny, with a reason that names the ctx tool to use; exempt: the index's skip dirs (`bin _templates sessions handoff memory state`), dot dirs, `INDEX.md`/`SESSION_INDEX.md` and what the store settings ignore (`README.md`). Not adopted, ctx missing, or any adapter error: no decision |
| `PostToolUse` on `Write`/`Edit`/`MultiEdit`/`NotebookEdit` of a file under the content root | `ctx validate --changed --adopt`, synchronous; findings come back as the hook's `systemMessage` (a `ctx validate: …` line); any other failure except `NO_STORE`, a timeout included, comes back as `ctx validate did not run: <first error line>`, so validation never stops unnoticed |
| the same, `async`, and a write through a ctx MCP tool | `ctx touch --session <session_id>`: the heartbeat of the registry row `session register` stamped with the harness session id; then `INDEX.md` and `SESSION_INDEX.md` are regenerated (`gen_index.py`, `gen_sessions.py --no-archive` — with or without ctx; a failure is logged to `hooks.log` in the scratch dir) |
| `SessionStart` `startup`/`resume`/`clear` | `ctx brief --registry`, byte-budgeted, into the session's context |
| `SessionStart` `compact` | `ctx brief --session <session_id>`, byte-budgeted |

Each is a silent no-op (exit 0, no output) when ctx is not installed or no store is named or found, so a machine
that has not adopted ctx-store sees nothing. The adapter names the store with `CTX_STORE` when set, else
`--store <content root>`. To check it: `python3 $BATON/context-db/bin/ctx_adapter.py where` (exit 1 says what is
missing), `… install` fetches the pinned tag, `KIT_CTX` points at another `ctx` (`docs/env-vars.md`). Changes made
through `Bash` are not seen by the hook; the next hooked write's `validate --changed` picks them up.

**"`<doc>` is a doc in the adopted ctx store … may not write it"** — the `PreToolUse` deny. Nothing is broken: write
the doc through the ctx MCP tools the reason names (`ctx_str_replace` for an `Edit`, `ctx_create` or `ctx_new` for
a `Write`, `ctx_log` for a Session-log line, `ctx_fm` for a frontmatter field), keyed by the path without `.md`.
When the session has no ctx tools (`/mcp` does not list `ctx` — restart after a plugin update, or on a clone
re-run `setup.sh`, which adds the server to the workspace `.mcp.json`), the same verbs run from Bash:
`python3 $BATON/context-db/bin/ctx_adapter.py ctx str_replace <key> --old "…" --new "…"` (`ctx help <verb>`). A
ctx tool answering `NO_STORE` means the store is not adopted: `python3 $BATON/context-db/bin/ctx_adapter.py adopt`
(once; it writes the kit's store settings where there are none and never overwrites; `/kit-health` § 5 reports it).

## 2. Start here: `/kit-health`

Whenever something about the kit seems off — a skill misbehaves, a session looks like it's missing rules, a hook
printed a line you don't understand — `/kit-health` is the first command, not a last resort. It is read-only
(only `--stamp` writes), checks the kit itself, this machine's env store, and the wiring the hooks above depend
on, and exits `0` GREEN, `1` AMBER, `2` RED. `skills/kit-health/SKILL.md` has the full walk; this page only
covers what a RED finding means and how to fix it.

## 3. The five most common RED findings

A RED finding is an `ERR`-level check; kit-health prints the section (`kit`, `config`, `machine`, `engine`, …)
with each one. These five cover most first-run and after-a-move problems:

| Finding (as kit-health prints it, trimmed) | Section | Fix |
|---|---|---|
| `env store missing` | config | `python3 $BATON/context-db/bin/kb.py init --blank`, then fill `config.json` (`kb.py config-set`) and facts (`kb.py set`) — or just re-run `sh $BATON/setup.sh`, which does the same and seeds everything else too |
| `no identity from any source: settings.local.json missing … and no WORKSPACE_* in the environment` | machine | `sh $BATON/setup.sh` seeds `settings.local.json` from the example (edit it), or `/plugin configure ai-baton` on a plugin install |
| `root CLAUDE.md missing` | machine | `sh $BATON/setup.sh` seeds it from `CLAUDE.example.md` — only when the file is absent, so this fires after a manual delete or a workspace root moved without it |
| `CLAUDE.md imports @.claude/WORKSPACE.md but the file is missing` (clone), or `CLAUDE.md does not import @.claude/WORKSPACE.md` | machine | add the line `@.claude/WORKSPACE.md` right after the preamble on a clone; on a plugin install this import should not exist at all — the plugin's SessionStart hook injects it (kit-health tells you which case you're in) |
| `kit-verify failed: …` | kit | the message quotes `kit_verify.py`'s own output — almost always a frontmatter or body rule on a skill/agent you just edited; `make -C $BATON/context-db verify-skill UNIT=skills/<name>` re-runs the same check on just that unit |

Every other RED and every AMBER is specific enough to act on directly from what kit-health prints; the walk in
`skills/kit-health/SKILL.md` § 4 (Fix / Ticket / Accept) covers how to resolve one once you understand it.

## 4. Uninstalling

`setup.sh` has no `--uninstall` flag: what it creates is either a file it seeds only when absent (safe to leave —
nothing re-creates or depends on it once the kit is gone) or a symlink/config value with one clear owner. The
manual list below removes exactly what `setup.sh` creates, nothing that holds your own content, and separately by
install mode (`docs/packaging.md` § Install mode names the three).

Two artifacts are common to both modes:

- **The harness memory symlink** — `~/.claude/projects/<workspace root with every / turned into ->/memory`,
  pointing at `.context/memory`. Remove the symlink (`rm <that path>`); your notes stay in `.context/memory`
  untouched, since the symlink is only the harness's side of the link.
- **`.context/`** (the env store, the context DB, memory, self-assessment, kit-health stamps) is your captured knowledge,
  not kit wiring — keep it unless you mean to discard that too. Delete it only as a deliberate last step, after
  the two mode-specific sections below.

**Clone** (`.claude/` is a git checkout of the kit):

1. `rm -rf .claude` — removes the checkout itself, `settings.local.json`, `.context/state/pr-review/config.json`'s
   seed source, and the kit's own git hooks config (`core.hooksPath`), since all of it lives inside that directory.
2. In the root `CLAUDE.md`, delete the lines `@.claude/WORKSPACE.md` and `@.context/reference/environment.md`
   (or delete the whole file if the kit created it and you never added your own preamble).
3. In the root `Makefile`, delete the line `include .claude/workspace.mk` — delete the file entirely when that
   was its only line.
4. Remove the harness memory symlink (above).
5. `rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/ai-baton-kit/ctx-store"` if you ran `ctx_adapter.py install` (the pinned
   ctx-store copy lives outside the checkout).

**Plugin** (`claude plugin install ai-baton@ai-baton-kit`):

1. `claude plugin uninstall ai-baton@ai-baton-kit` removes it from Claude Code's plugin cache.
2. `claude plugin marketplace remove ai-baton-kit`, if you added no other plugin from that marketplace.
3. Remove `<workspace root>/.claude/settings.local.json` (a plugin install keeps identity beside the workspace,
   never in the plugin cache) — delete `<workspace root>/.claude/` entirely if the kit was the only thing that
   used it.
4. In the root `CLAUDE.md`, delete the line `@.context/reference/environment.md` (a plugin install's `CLAUDE.md`
   never imports `@.claude/WORKSPACE.md` — that came from the SessionStart hook at runtime, so there is no line
   on disk for it to leave behind).
5. Remove the harness memory symlink (above).
6. `rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/ai-baton-kit/ctx-store"` if you ran `ctx_adapter.py install` (the pinned
   ctx-store copy lives outside the plugin cache).

Either way, `/kit-health` won't run again once step 1 is done — there is nothing left to check.
