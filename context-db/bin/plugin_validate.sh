#!/bin/sh
# plugin_validate.sh — the plugin and marketplace manifests, every skill and every agent, through Claude Code's
# own validator (`claude plugin validate`; docs/packaging.md). `make -C context-db plugin-validate` runs it, from
# `make ci` and ci.yml; the Makefile handles a machine without the `claude` CLI.
#
# Strict everywhere (a warning is a failure) with ONE accepted warning at the plugin root: from CLI 2.1.28x on,
# "CLAUDE.md at the plugin root is not loaded as project context". That file is the kit's memory on a CLONE
# install, where Claude Code loads `.claude/CLAUDE.md` into the workspace session (docs/layout.md); a plugin
# install never loads it, which is exactly what the warning says and what the kit documents. So the root runs
# without --strict and any OTHER warning fails here, by hand (#521); skills/ and agents/ stay --strict.
#
# Exit: 0 clean; the validator's own code on an error; 1 on a warning beyond the accepted one.
set -u
cd "$(dirname "$0")/../.." || exit 2

ACCEPTED='CLAUDE.md at the plugin root is not loaded as project context'

out=$(claude plugin validate . 2>&1); rc=$?
printf '%s\n' "$out"
[ "$rc" -eq 0 ] || exit "$rc"
# each warning is one `❯` line under "Found N warning(s)"; drop the accepted one and anything left is a failure
unexpected=$(printf '%s\n' "$out" | grep -F '❯' | grep -F -v "$ACCEPTED")
if [ -n "$unexpected" ]; then
  echo "plugin validate: FAILED — warning(s) beyond the accepted root CLAUDE.md one (treated as errors, like --strict):" >&2
  printf '%s\n' "$unexpected" >&2
  exit 1
fi
claude plugin validate --strict skills >/dev/null || exit 1
claude plugin validate --strict agents >/dev/null || exit 1
echo "plugin validate: OK"
