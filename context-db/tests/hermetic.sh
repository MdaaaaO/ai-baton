#!/bin/sh
# tests/hermetic.sh — the git isolation every sh fixture that spawns git needs, sourced by setup_sh_scenarios.sh
# (tests/__init__.py's hermetic_env() gives the Python suite the same shape). A host's own commit.gpgsign,
# gpg.format or system git config must never reach a throw-away fixture repo: `sh -e`, POSIX only, no bashisms.
#
#   . "$(dirname "$0")/hermetic.sh"; hermetic_git_env "$some_tmp_dir"

hermetic_git_env() {  # hermetic_git_env <home-dir> → exports HOME/TMPDIR and disables inherited git config
  export HOME="$1"
  export TMPDIR="$1"
  export GIT_CONFIG_GLOBAL=/dev/null
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_TERMINAL_PROMPT=0
  export GIT_AUTHOR_NAME=t
  export GIT_AUTHOR_EMAIL=t@example.invalid
  export GIT_COMMITTER_NAME=t
  export GIT_COMMITTER_EMAIL=t@example.invalid
}
