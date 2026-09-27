#!/usr/bin/env bash
# setup.sh — make this Claude Code workspace usable on this machine / sandbox.
# Idempotent: safe to run any number of times. Run it once per fresh clone or sandbox
# (a sandbox recreate WIPES ~/.claude but keeps the host-mounted workspace tree).
#
#   sh .claude/setup.sh [--force] [--personal]  # from the workspace root (the dir containing .claude/ and .context/)
#                                  # --force: repoint a memory symlink that points somewhere else (see 2.)
#                                  # --personal: the zero-config GitHub-only path (see 4.) — one command, no questions
#
# Normally Claude Code runs this for you — the install paths are README.md § Install.
#
# What it does:
#   1. Creates the personal content dirs under ../.context/ (memory/, state/pr-review/).
#   2. Points the harness auto-memory dir (~/.claude/projects/<slug>/memory, wiped on recreate)
#      at the durable copy ../.context/memory via a symlink — so memory survives and is yours. A real
#      dir with notes is migrated first: every file is copied to a staging dir and the count verified
#      BEFORE the original is removed (a failed copy leaves it untouched and exits 1; a dir holding a
#      non-regular entry — a symlink, a pipe — is refused with exit 1, since a copy cannot carry it); a
#      symlink that already points at something else is reported and left alone unless --force, while a
#      DANGLING one (target absent) is repointed.
#   3. Seeds .claude/settings.local.json (ignored, personal identity env) from the example, and
#      .context/state/pr-review/config.json from pr-review/config.example.json, if absent.
#   4. Makes sure this environment has its configuration: the env fact store
#      .context/reference/env/ (config.json + per-system fact tables, `context-db/bin/kb.py`),
#      blank when missing. With
#      --personal the blank store is FILLED without a question (context-db/bin/personal.py): tracker =
#      GitHub issues, tracked repos = the clones under the workspace root, identity from `gh api user`
#      (also written into settings.local.json and the pr-review config where still empty), timezone from
#      the OS, every systems.* false; a configured value is never overwritten, so it is idempotent. Then
#      seeds the environment prose .context/reference/environment.md (from
#      environment-template/environment.md), the root CLAUDE.md (your preamble +
#      `@.claude/WORKSPACE.md` + `@.context/reference/environment.md`), the root Makefile
#      (`include .claude/workspace.mk`), .context/README.md (the DB spec) and
#      .context/self-assessment/README.md (the self-assessment charter, from
#      context-db/_templates/self-assessment-charter.md) — each only if
#      absent; existing files are reported, never touched — a later change to a template never reaches
#      an existing machine by itself: `--refresh-seeds` prints the diff between each seeded copy and its
#      template (kit-health warns when a copy predates its template), you merge what you want by hand.
#      Removes the bytecode caches and empty directories git never tracks (what a removed skill leaves behind).
#   5. Reports skill discovery.
#
# Nothing here is user- or machine-specific: the root is derived from this script's location when it is a
# `.claude/` clone, else from CLAUDE_PROJECT_DIR / the current directory (a plugin install or a dev checkout —
# docs/packaging.md § Install mode) (override with PROJECTS=/path if you must). The install mode is
# `kit_profile.py install-mode` (clone | plugin | dev-checkout), recorded in the env store as `kit.install_mode`.
set -eu

FORCE=0
PERSONAL=0
REFRESH=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --personal) PERSONAL=1 ;;
    --refresh-seeds) REFRESH=1 ;;
    -h|--help) sed -n '2,/^[^#]/{/^#/p;}' "$0"; exit 0 ;;   # the whole comment header, however long it grows
    *) echo "setup.sh: unknown argument '$arg' (only --force, --personal, --refresh-seeds)" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
# clone | plugin | dev-checkout — the one rule (#34), kit_profile.install_mode(); every mode-dependent step below follows it
MODE="$(python3 "$HERE/context-db/bin/kit_profile.py" install-mode)" || { echo "setup.sh: kit_profile.py install-mode failed (see above)" >&2; exit 1; }
# how a hint names the kit for a shell in the workspace root: `.claude` on a clone, else the kit's own path
KITREF="$(python3 "$HERE/context-db/bin/kit_profile.py" mode-hint kit_ref)" || { echo "setup.sh: kit_profile.py mode-hint failed (see above)" >&2; exit 1; }
# The workspace root: the parent of a `.claude/` clone. Run from anywhere else (a plugin install lives in Claude Code's
# plugin cache, #118) the parent is the cache, so the root is CLAUDE_PROJECT_DIR, else the current directory — never a
# directory an update may delete. PROJECTS=/path overrides all of it.
if [ -z "${PROJECTS:-}" ]; then
  if [ "$MODE" = clone ]; then PROJECTS="$(dirname "$HERE")"
  elif [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then PROJECTS="$CLAUDE_PROJECT_DIR"
  else PROJECTS="$PWD"; fi
fi
# Never the home directory: `~/.claude` is Claude Code's own config dir, so a clone there overwrites the harness's
# settings and puts `.context/` in $HOME (#168). Pick a workspace dir that holds your repos, e.g. ~/Projects.
if [ "$(cd "$PROJECTS" 2>/dev/null && pwd -P)" = "$(cd "$HOME" && pwd -P)" ]; then
  if [ "$MODE" = clone ]; then echo "setup.sh: refusing the home directory as the workspace root ($PROJECTS) — clone the kit into e.g. ~/Projects/.claude instead (#168)" >&2
  else echo "setup.sh: refusing the home directory as the workspace root ($PROJECTS) — run it from the project directory, or set CLAUDE_PROJECT_DIR / PROJECTS=/path (#168)" >&2; fi
  exit 2
fi
CONTEXT="$PROJECTS/.context"
DURABLE_MEM="$CONTEXT/memory"
# Where the ignored settings.local.json lives: beside the kit on the clone path, else `<root>/.claude/` — the one
# directory Claude Code reads project settings from. Never the plugin cache: an update would delete the identity.
if [ "$MODE" = clone ]; then SETTINGS_DIR="$HERE"; else SETTINGS_DIR="$PROJECTS/.claude"; fi
SETTINGS="$SETTINGS_DIR/settings.local.json"

# The harness derives its per-project dir by replacing '/' with '-' in the project path.
SLUG="$(printf '%s' "$PROJECTS" | sed 's#/#-#g')"
HARNESS_MEM="$HOME/.claude/projects/$SLUG/memory"

# seed SRC DST — byte-verified copy. Never `cp`: across a sandbox bind mount `cp` can produce a
# right-length, NUL-filled file (seen 2026-09-17 and again here on 2026-09-25); cat + cmp catches it.
seed() {
  cat "$1" > "$2" && cmp -s "$1" "$2" || { echo "  ERROR: copy of $1 -> $2 is corrupt (sandbox mount?) — removed; re-run setup.sh" >&2; rm -f "$2"; return 1; }
}

echo "== workspace setup =="
echo "  workspace root: $PROJECTS"
echo "  durable memory: $DURABLE_MEM"
echo "  harness memory: $HARNESS_MEM"

mkdir -p "$DURABLE_MEM" "$CONTEXT/state/pr-review"

if [ -L "$HARNESS_MEM" ]; then
  # an existing link: fine when it already resolves to the durable dir; a DANGLING link (its target is ABSENT — a moved
  # workspace, a recreated .context/) holds nothing and is repointed; a link to anything that exists — another dir, a
  # dir that cannot be entered, a file — may hold notes that a silent repoint would orphan: say so and leave it, unless
  # --force (merge the notes by hand first). Absence is tested directly (`-e` follows the link); a failing `cd` is not
  # taken as "gone".
  target_now="$(cd "$HARNESS_MEM" 2>/dev/null && pwd -P)" || target_now=""
  durable_now="$(cd "$DURABLE_MEM" && pwd -P)"
  if [ -n "$target_now" ] && [ "$target_now" = "$durable_now" ]; then
    echo "  memory: symlink present -> durable"
  elif [ ! -e "$HARNESS_MEM" ]; then
    ln -sfn "$DURABLE_MEM" "$HARNESS_MEM"
    echo "  memory: symlink was dangling ($(readlink "$HARNESS_MEM" 2>/dev/null || echo '?') is gone) — repointed -> durable"
  elif [ "$FORCE" = 1 ]; then
    ln -sfn "$DURABLE_MEM" "$HARNESS_MEM"
    echo "  memory: symlink repointed from ${target_now:-$(readlink "$HARNESS_MEM")} -> durable (--force)"
  else
    echo "  WARNING: memory symlink points to ${target_now:-$(readlink "$HARNESS_MEM")}, not $DURABLE_MEM — left alone; notes there would be orphaned by a repoint." >&2
    echo "           Merge them into $DURABLE_MEM by hand, then re-run with --force." >&2
  fi
elif [ -d "$HARNESS_MEM" ]; then
  # a real dir with (possibly) fresh notes — stage a verified copy of EVERY file, check the count, and only then
  # merge the not-yet-durable ones and remove the original. Any failure leaves the original untouched and exits 1.
  # only regular files are staged and counted; anything else (a symlink, a FIFO, a socket) would be neither, yet
  # `rm -rf` below would still delete it — refuse rather than break the "nothing removed that was not copied" promise
  n_other="$(cd "$HARNESS_MEM" && find . ! -type d ! -type f | wc -l | tr -d ' ')"
  if [ "$n_other" != 0 ]; then
    echo "  ERROR: $HARNESS_MEM holds $n_other entr(y/ies) that are not regular files (symlinks, pipes…) — a copy cannot carry them; move or remove them by hand, then re-run. Left untouched, nothing linked" >&2
    (cd "$HARNESS_MEM" && find . ! -type d ! -type f | sed 's|^\./|    |') >&2
    exit 1
  fi
  STAGE="$(mktemp -d "$CONTEXT/.memory-migrate.XXXXXX")"
  n_src="$(cd "$HARNESS_MEM" && find . -type f | wc -l | tr -d ' ')"
  copy_ok=1
  ( cd "$HARNESS_MEM" && find . -type f | while IFS= read -r f; do
      mkdir -p "$STAGE/$(dirname "$f")" && seed "$f" "$STAGE/$f" || exit 1
    done ) || copy_ok=0
  n_stage="$(cd "$STAGE" && find . -type f | wc -l | tr -d ' ')"
  if [ "$copy_ok" != 1 ] || [ "$n_src" != "$n_stage" ]; then
    echo "  ERROR: memory migration incomplete ($n_stage of $n_src files copied) — $HARNESS_MEM left untouched, nothing linked; fix the cause and re-run" >&2
    rm -rf "$STAGE"
    exit 1
  fi
  n_new=0
  n_kept=0
  list="$STAGE.list"
  (cd "$STAGE" && find . -type f | sed 's|^\./||') > "$list"
  while IFS= read -r f; do   # a list file, not `for $(find)`: a note name with a space stays one name
    if [ -e "$DURABLE_MEM/$f" ]; then
      n_kept=$((n_kept + 1))   # the durable copy is the one sessions have been reading — it wins
    else
      mkdir -p "$DURABLE_MEM/$(dirname "$f")" && mv "$STAGE/$f" "$DURABLE_MEM/$f" || {
        echo "  ERROR: could not move $f into $DURABLE_MEM — $HARNESS_MEM left untouched (staged copy in $STAGE)" >&2; exit 1; }
      n_new=$((n_new + 1))
    fi
  done < "$list"
  rm -f "$list"
  rm -rf "$STAGE"
  rm -rf "$HARNESS_MEM"
  ln -sfn "$DURABLE_MEM" "$HARNESS_MEM"
  echo "  memory: migrated real dir -> durable ($n_src files verified, $n_new new, $n_kept already durable), replaced with symlink"
else
  mkdir -p "$(dirname "$HARNESS_MEM")"
  ln -sfn "$DURABLE_MEM" "$HARNESS_MEM"
  echo "  memory: created symlink -> durable"
fi

if [ -w "$HARNESS_MEM" ]; then
  echo "  memory: OK ($(ls -1 "$HARNESS_MEM"/*.md 2>/dev/null | wc -l | tr -d ' ') files via link)"
else
  echo "  memory: WARNING — link created but not writable; check $HARNESS_MEM"
fi

echo
echo "== personal config =="
if [ ! -f "$SETTINGS" ]; then
  mkdir -p "$SETTINGS_DIR"
  seed "$HERE/settings.local.example.json" "$SETTINGS"
  if [ "$PERSONAL" = 1 ]; then echo "  created .claude/settings.local.json from the example — identity filled from gh below"
  else echo "  created .claude/settings.local.json from the example — EDIT IT (WORKSPACE_USER, WORKSPACE_GITHUB_LOGIN, WORKSPACE_TZ, …), or on a plugin install run /plugin configure ai-baton instead"; fi
else
  echo "  .claude/settings.local.json present"
fi
# On a plugin install the file sits in the user's project, outside the kit's .gitignore: say so once when that project is a
# git repo that does not ignore it, so an identity never lands in a commit (#118 review nit).
if [ "$SETTINGS_DIR" != "$HERE" ] && git -C "$PROJECTS" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
   && ! git -C "$PROJECTS" check-ignore -q "$SETTINGS" 2>/dev/null; then
  echo "  WARNING: $SETTINGS is not git-ignored in $PROJECTS — add '.claude/settings.local.json' to its .gitignore before committing"
fi
if [ ! -f "$CONTEXT/state/pr-review/config.json" ]; then
  seed "$HERE/pr-review/config.example.json" "$CONTEXT/state/pr-review/config.json"
  echo "  created .context/state/pr-review/config.json from the example — set \"login\" to your GitHub login"
else
  echo "  .context/state/pr-review/config.json present"
fi
identity_status() {
  for v in WORKSPACE_USER WORKSPACE_GITHUB_LOGIN WORKSPACE_TZ; do
    # POSIX ERE (`+`), not the GNU-only BRE `\+` — on BSD/macOS grep every key read "NOT SET"
    if grep -Eq "\"$v\": *\"[^\"]+\"" "$SETTINGS" 2>/dev/null; then echo "  $v: set"; else echo "  $v: NOT SET in settings.local.json"; fi
  done
}
if [ "$PERSONAL" = 1 ]; then echo "  (--personal: identity is filled from gh below)"; else identity_status; fi

echo
echo "== configuration: env fact store + environment prose =="
# Where this environment's facts live: .context/reference/env/ (config.json + <system>.md tables,
# managed by context-db/bin/kb.py — local, grows as the kit is used). Its prose (repo map, venues,
# environment-only skills) is .context/reference/environment.md, imported by the root CLAUDE.md.
ENV_DIR="$CONTEXT/reference/env"
ENV_DOC="$CONTEXT/reference/environment.md"
if [ ! -f "$ENV_DIR/config.json" ]; then
  # a failed init is a failed setup, never a swallowed line: the store is what every later step reads
  init_out="$(CONTEXT_ROOT="$CONTEXT" python3 "$HERE/context-db/bin/kb.py" init --blank)" || { echo "  ERROR: kb.py init --blank failed — see above; setup stopped" >&2; exit 1; }
  printf '%s\n' "$init_out" | sed 's/^/  /'
  if [ "$PERSONAL" != 1 ]; then
    echo "  → fill it: kb.py config-set environment <name>, tracker.kind jira|github, github.org <org>, systems.<x> true|false;"
    echo "    facts as you learn them: kb.py set slack.channel <name> <id> --purpose … --from tool:<tool-name>|user"
  fi
else
  echo "  env store present: $ENV_DIR ($(ls -1 "$ENV_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ') system tables)"
fi
if [ "$PERSONAL" = 1 ]; then
  # the zero-config path: discover (gh, the workspace clones, the OS zone), write only what is still blank, never ask.
  # A failed run stops the setup like a failed init would — the value of a captured status, not a pipe's.
  personal_out="$(CONTEXT_ROOT="$CONTEXT" python3 "$HERE/context-db/bin/personal.py" --workspace "$PROJECTS" \
      --settings "$SETTINGS" --pr-review "$CONTEXT/state/pr-review/config.json")" \
    || { echo "  ERROR: personal.py failed — see above; setup stopped" >&2; exit 1; }
  printf '%s\n' "$personal_out" | sed 's/^/  /'
  identity_status
fi
ACTIVE="$(CONTEXT_ROOT="$CONTEXT" python3 "$HERE/context-db/bin/kit_profile.py" name)" || { echo "  ERROR: kit_profile.py name failed — the env store's config.json is unreadable (see above); setup stopped" >&2; exit 1; }
echo "  active environment: $ACTIVE (config.json \`environment\`)"
# Record the install mode (#34) so kit-health § 1 can tell when the kit it runs from is installed another way. A dev
# checkout beside this workspace's `.claude/` clone keeps `clone` recorded (`install-mode --to-record`).
WANT="$(CONTEXT_ROOT="$CONTEXT" python3 "$HERE/context-db/bin/kit_profile.py" install-mode --to-record)" || { echo "  ERROR: kit_profile.py install-mode failed; setup stopped" >&2; exit 1; }
# `get` exits 1 only for an unset key here — the store was read above (`name`), so an unreadable one already stopped us
REC="$(CONTEXT_ROOT="$CONTEXT" python3 "$HERE/context-db/bin/kit_profile.py" get kit.install_mode)" || REC=""
if [ "$WANT" != "$MODE" ]; then
  echo "  install mode: $MODE — a development checkout beside this workspace's .claude/ clone; kit.install_mode stays $WANT"
elif [ "$REC" = "$MODE" ]; then
  echo "  install mode: $MODE (kit.install_mode)"
else
  CONTEXT_ROOT="$CONTEXT" python3 "$HERE/context-db/bin/kb.py" config-set kit.install_mode "$MODE" >/dev/null || { echo "  ERROR: kb.py config-set kit.install_mode failed; setup stopped" >&2; exit 1; }
  echo "  install mode: $MODE — recorded in kit.install_mode${REC:+ (was $REC)}"
  if [ "$REC" = clone ]; then
    echo "  the old clone's wiring may be left: a .claude/ clone, @.claude/WORKSPACE.md in CLAUDE.md, include .claude/workspace.mk — the lines below name what is still there"
  fi
fi
# housekeeping (#78): bytecode caches and empty directories are never tracked by git, so a removed skill leaves them
# behind on every machine until something deletes them — this does, inside the kit only (never the workspace).
# Non-fatal (setup runs under set -e): an unreadable or vanishing entry ends the sweep with one visible line, not the
# setup. Dot-directories at the kit root are never touched: `.sync.lock.d` is sync.sh's mkdir lock on hosts without flock.
sweep_rc=0; sweep_err="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/setup-sweep.$$")"
sweep() { "$@" 2>>"$sweep_err" || sweep_rc=1; }  # the status is find's own — POSIX sh has no pipefail
for d in "$HERE"/*/; do  # a top-level symlink is never followed: the sweep stays inside the kit
  [ -d "$d" ] && [ ! -L "${d%/}" ] || continue
  sweep find "$d" -name __pycache__ -type d -prune -exec rm -rf {} +
  sweep find "$d" -mindepth 1 -type d -empty -delete
done
sweep find "$HERE" -mindepth 1 -maxdepth 1 -type d -not -name '.*' -empty -delete
if [ "$sweep_rc" -ne 0 ]; then
  sed 's/^/  housekeeping: /' "$sweep_err" >&2
  echo "  WARNING: housekeeping sweep incomplete (see the housekeeping: lines above) — nothing else is affected" >&2
fi
rm -f "$sweep_err"

echo "== workspace files (seeded only if absent) =="
if [ ! -f "$ENV_DOC" ]; then
  mkdir -p "$(dirname "$ENV_DOC")"
  seed "$HERE/environment-template/environment.md" "$ENV_DOC"
  sed -i.bak -e "s|<name>|$ACTIVE|g" -e "s|<YYYY-MM-DD>|$(date +%F)|" "$ENV_DOC" && rm -f "$ENV_DOC.bak"
  echo "  created .context/reference/environment.md from environment-template/environment.md — FILL IT (repos, venues, capabilities), then make -C $KITREF/context-db index"
else
  echo "  .context/reference/environment.md present"
fi
# A plugin install (#3) and a dev checkout (run like one, `--plugin-dir`) have no kit files in <root>/.claude/: the
# plugin's SessionStart hook injects WORKSPACE.md
# (`kit_profile.py workspace-rules`), so the seeded CLAUDE.md drops that import and the Makefile gets no include —
# workspace.mk's targets (claude_sync, sign*, releases) drive a `.claude/` clone.
# CLAUDE_TPL is the template this path seeds from — also what --refresh-seeds diffs against, so it never proposes the
# import back.
if [ "$MODE" = clone ]; then CLONE=1; CLAUDE_TPL="$HERE/CLAUDE.example.md"
else
  CLONE=0; CLAUDE_TPL="$(mktemp "${TMPDIR:-/tmp}/claude-md-tpl.XXXXXX")"; trap 'rm -f "$CLAUDE_TPL"' EXIT
  sed '/^@\.claude\/WORKSPACE\.md$/d' "$HERE/CLAUDE.example.md" > "$CLAUDE_TPL"
fi
if [ ! -f "$PROJECTS/CLAUDE.md" ] && [ "$CLONE" -eq 0 ]; then
  seed "$CLAUDE_TPL" "$PROJECTS/CLAUDE.md"
  echo "  created CLAUDE.md from CLAUDE.example.md — EDIT the preamble (who you are); it imports .context/reference/environment.md (the plugin's SessionStart hook injects WORKSPACE.md)"
elif [ ! -f "$PROJECTS/CLAUDE.md" ]; then
  seed "$HERE/CLAUDE.example.md" "$PROJECTS/CLAUDE.md"
  echo "  created CLAUDE.md from $KITREF/CLAUDE.example.md — EDIT the preamble (who you are); it imports .claude/WORKSPACE.md + .context/reference/environment.md"
else
  if grep -q '^@.claude/WORKSPACE.md' "$PROJECTS/CLAUDE.md"; then IMP=1; else IMP=0; fi
  if [ -f "$PROJECTS/.claude/WORKSPACE.md" ]; then WSF=1; else WSF=0; fi
  if [ "$CLONE" -eq 0 ] && [ "$IMP" -eq 1 ] && [ "$WSF" -eq 0 ]; then
    echo "  CLAUDE.md imports .claude/WORKSPACE.md, which a plugin install does not have — remove that line (the plugin's SessionStart hook injects WORKSPACE.md)"
  elif [ "$CLONE" -eq 0 ] && [ "$IMP" -eq 1 ]; then
    echo "  CLAUDE.md imports .claude/WORKSPACE.md (a copy in .claude/ — the plugin's hook stays quiet so it does not load twice)"
  elif [ "$CLONE" -eq 0 ] && [ "$WSF" -eq 1 ]; then
    echo "  .claude/WORKSPACE.md exists but CLAUDE.md does NOT import it, and the plugin's hook skips a workspace with its own copy — WORKSPACE.md loads nowhere: add the line @.claude/WORKSPACE.md, or delete the copy"
  elif [ "$CLONE" -eq 0 ]; then
    echo "  CLAUDE.md present (WORKSPACE.md comes from the plugin's SessionStart hook)"
  elif grep -q '^@.claude/WORKSPACE.md' "$PROJECTS/CLAUDE.md"; then
    echo "  CLAUDE.md present, imports .claude/WORKSPACE.md"
  else
    echo "  CLAUDE.md present but does NOT import the shared body — add a line: @.claude/WORKSPACE.md"
  fi
  if grep -q '^@.context/reference/environment.md' "$PROJECTS/CLAUDE.md"; then
    echo "  CLAUDE.md imports .context/reference/environment.md"
  else
    if [ "$CLONE" -eq 1 ]; then echo "  CLAUDE.md does NOT import the environment prose — add a line right after @.claude/WORKSPACE.md: @.context/reference/environment.md"
    else echo "  CLAUDE.md does NOT import the environment prose — add a line: @.context/reference/environment.md"; fi
  fi
fi
if [ "$CLONE" -eq 0 ]; then
  if [ -f "$PROJECTS/Makefile" ] && grep -q '^include .claude/workspace.mk' "$PROJECTS/Makefile" && [ ! -f "$PROJECTS/.claude/workspace.mk" ]; then
    echo "  Makefile includes .claude/workspace.mk, which a plugin install does not have — every make fails: remove that line"
  elif [ -f "$PROJECTS/Makefile" ] && grep -q '^include .claude/workspace.mk' "$PROJECTS/Makefile"; then
    echo "  Makefile present, includes .claude/workspace.mk (the file exists)"
  else
    echo "  Makefile: nothing to include on a $MODE install (workspace.mk drives a .claude/ clone)"
  fi
elif [ ! -f "$PROJECTS/Makefile" ]; then
  printf '# Workspace-level helpers. Shared targets (sign*, claude_sync, ctx_*) come from the kit:\ninclude .claude/workspace.mk\n' > "$PROJECTS/Makefile"
  echo "  created Makefile with 'include .claude/workspace.mk'"
elif grep -q '^include .claude/workspace.mk' "$PROJECTS/Makefile"; then
  echo "  Makefile present, includes .claude/workspace.mk"
else
  echo "  Makefile present but does NOT include .claude/workspace.mk — add that line for make sign / claude_sync"
fi
if [ -d "$HERE/.git" ]; then
  if [ "$(git -C "$HERE" config --get core.hooksPath 2>/dev/null)" = "$HERE/hooks" ]; then
    echo "  kit git hooks installed (core.hooksPath=$KITREF/hooks: pre-push main guard + commit-msg style check)"
  else
    git -C "$HERE" config core.hooksPath "$HERE/hooks" && echo "  installed the kit git hooks (core.hooksPath=$KITREF/hooks: pre-push main guard + commit-msg Conventional Commits check) — the kit's main is PR-only: branch + PR, then make claude_sync"
  fi
fi
if [ ! -f "$CONTEXT/README.md" ]; then
  seed "$HERE/context-db/context-README.template.md" "$CONTEXT/README.md"
  echo "  created .context/README.md (the DB spec) from the template"
else
  echo "  .context/README.md present"
fi
if [ ! -f "$CONTEXT/self-assessment/README.md" ]; then
  mkdir -p "$CONTEXT/self-assessment/weeks"
  seed "$HERE/context-db/_templates/self-assessment-charter.md" "$CONTEXT/self-assessment/README.md"
  echo "  created .context/self-assessment/README.md (the self-assessment charter) from the template"
else
  echo "  .context/self-assessment/README.md present"
fi

# Seeds vs templates (#79): setup never overwrites a seeded file, so a template fixed after the seed stays fixed only in
# the kit. Compare the four pairs: the template's last commit time against the copy's mtime; a copy older than that is
# reported (kit-health § 4 warns the same), and --refresh-seeds prints the unified diff so you can merge by hand.
seed_pairs() {
  printf '%s\t%s\n' "$HERE/context-db/context-README.template.md" "$CONTEXT/README.md"
  printf '%s\t%s\n' "$HERE/environment-template/environment.md" "$ENV_DOC"
  printf '%s\t%s\t%s\n' "$HERE/CLAUDE.example.md" "$PROJECTS/CLAUDE.md" "$CLAUDE_TPL"  # age from the kit file, diff vs what was seeded
  printf '%s\t%s\n' "$HERE/context-db/_templates/self-assessment-charter.md" "$CONTEXT/self-assessment/README.md"
}
stale_seeds=0
while IFS="$(printf '\t')" read -r tpl copy difftpl; do
  difftpl="${difftpl:-$tpl}"  # the file the copy was seeded from (the plugin path trims CLAUDE.md's import, #3)
  [ -f "$tpl" ] && [ -f "$copy" ] || continue
  tpl_t="$(git -C "$HERE" log -1 --format=%ct -- "$tpl" 2>/dev/null || echo 0)"
  copy_t="$(python3 -c 'import os,sys; print(int(os.path.getmtime(sys.argv[1])))' "$copy" 2>/dev/null || echo 0)"
  if [ -z "$tpl_t" ] || [ "$tpl_t" = 0 ]; then  # no git history for the template (plugin install): nothing to compare against
    [ "$REFRESH" = 1 ] && { echo "  seed ${copy#"$PROJECTS"/}: cannot compare — the kit has no git history here; diff (copy → template):"; diff -u "$copy" "$difftpl" | sed 's/^/    /' || true; }
    continue
  fi
  if [ "${tpl_t:-0}" -gt "${copy_t:-0}" ] 2>/dev/null; then
    stale_seeds=$((stale_seeds + 1))
    if [ "$REFRESH" = 1 ]; then
      echo "  seed ${copy#"$PROJECTS"/} predates its template ${tpl#"$HERE"/} — diff (copy → template):"
      diff -u "$copy" "$difftpl" | sed 's/^/    /' || true
    fi
  elif [ "$REFRESH" = 1 ] && ! cmp -s "$difftpl" "$copy"; then
    echo "  seed ${copy#"$PROJECTS"/} differs from ${tpl#"$HERE"/} (your edits; the template is not newer) — no action"
  fi
done <<EOF_SEEDS
$(seed_pairs)
EOF_SEEDS
if [ "$stale_seeds" -gt 0 ] && [ "$REFRESH" != 1 ]; then
  echo "  $stale_seeds seeded file(s) predate their template — sh $KITREF/setup.sh --refresh-seeds shows the diffs"
fi

if [ "$PERSONAL" = 1 ]; then
  # the first /kit-health on this machine should start from a fresh index, not a "stale INDEX.md" warning
  if CONTEXT_ROOT="$CONTEXT" python3 "$HERE/context-db/bin/gen_index.py" >/dev/null 2>&1; then
    echo "  indexed .context/ (INDEX.md) so the first /kit-health starts clean"
  else
    echo "  WARNING: could not index .context/ — run: make -C $KITREF/context-db index" >&2
  fi
fi

echo
echo "== skills =="
# label the kit's skills by where they are (#69): the workspace's .claude/skills only on a clone — elsewhere $HERE is the
# plugin cache or a dev checkout, and the workspace has no .claude/skills
KIT_SKILLS="$(ls -1 "$HERE/skills" 2>/dev/null | tr '\n' ' ')"
case "$MODE" in
  clone) echo "  workspace ($KITREF/skills): $KIT_SKILLS" ;;
  plugin) echo "  kit skills (plugin $(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("version") or "?")' "$HERE/.claude-plugin/plugin.json" 2>/dev/null || echo "?"), $HERE/skills): $KIT_SKILLS" ;;
  *) echo "  kit skills (dev checkout $HERE): $KIT_SKILLS" ;;
esac
echo "  user-level (~/.claude/skills): $(ls -1 "$HOME/.claude/skills" 2>/dev/null | tr '\n' ' ')"
case "$MODE" in
  clone) echo "  (both dirs are discovered each session.)" ;;
  plugin) echo "  (the plugin's skills and the user-level ones are discovered each session.)" ;;
  *) echo "  (a dev checkout's skills load in a session started with claude --plugin-dir $HERE; the user-level ones in every session.)" ;;
esac
echo
echo "setup complete."
