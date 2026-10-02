#!/bin/sh
# tests/hermetic.sh — the git isolation every sh fixture that spawns git needs, sourced by setup_sh_scenarios.sh
# (tests/__init__.py's hermetic_env() gives the Python suite the same shape). A host's own commit.gpgsign,
# gpg.format or system git config must never reach a throw-away fixture repo: `sh -e`, POSIX only, no bashisms.
# Config only: GIT_DIR and the other per-repository GIT_* variables of the caller are left as they are.
#
#   . "$(dirname "$0")/hermetic.sh"; hermetic_git_env "$some_tmp_dir"

hermetic_git_env() {  # hermetic_git_env <home-dir> (absolute) → exports HOME/TMPDIR, keeps the host's git config
  # out, and turns git's background housekeeping off: `git commit`, and the receive-pack of a push into a local
  # origin, can start a detached `git maintenance run --auto` that still writes into .git while the fixture is
  # removed. The two switches sit in a global config file under <home-dir>, not in GIT_CONFIG_COUNT entries:
  # git drops those before it starts receive-pack.
  export HOME="$1"
  export TMPDIR="$1"
  printf '[gc]\n\tauto = 0\n[maintenance]\n\tauto = false\n' > "$1/.hermetic-gitconfig"
  export GIT_CONFIG_GLOBAL="$1/.hermetic-gitconfig"
  export GIT_CONFIG_NOSYSTEM=1
  unset GIT_CONFIG_SYSTEM GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS  # each would outrank or replace the lines above
  export GIT_TERMINAL_PROMPT=0
  export GIT_AUTHOR_NAME=t
  export GIT_AUTHOR_EMAIL=t@example.invalid
  export GIT_COMMITTER_NAME=t
  export GIT_COMMITTER_EMAIL=t@example.invalid
}

kit_copy_tracked() {  # kit_copy_tracked <src-repo> <dest-dir> → copies every git-TRACKED file out of <src-repo>'s
  # working tree into <dest-dir>, building the tree from `git ls-files` rather than `find`/`cp` over the whole
  # checkout: an untracked file lying around in a developer's checkout (a node_modules/, a scratch file, a stray
  # .context) must never leak into a fixture. `-c safe.directory` trusts <src-repo> the same way
  # tests/__init__.py's hermetic_env(trust=...) does for the Python suite — hermetic_git_env above already
  # disabled the global/system git config, which would otherwise make git refuse a checkout it does not own.
  src="$1"; dest="$2"
  mkdir -p "$dest"
  # the list is captured first: in `git … | while`, git's own failure (not a checkout, trust refused) would be
  # lost to the loop's status and leave a silently empty copy
  files=$(git -C "$src" -c safe.directory="$src" ls-files) || { echo "kit_copy_tracked: git ls-files failed in $src" >&2; return 1; }
  [ -n "$files" ] || { echo "kit_copy_tracked: no tracked files in $src" >&2; return 1; }
  printf '%s\n' "$files" | while IFS= read -r f; do
    [ -e "$src/$f" ] || continue  # tracked but deleted in the working tree
    mkdir -p "$dest/$(dirname "$f")"
    cp "$src/$f" "$dest/$f"
  done
}
