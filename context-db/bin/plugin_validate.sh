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
# Fails closed: the root pass is accepted only when its output never mentions a warning (the older CLI) or
# counts exactly one ("Found 1 warning:") AND that one is the accepted text. A different count, a count the
# script can't read (a CLI that reworded the banner) or a lone warning that isn't the accepted one all fail,
# so a format change can never let a new warning through as "OK".
#
# Exit: 0 clean; the validator's own code on an error; 1 on a warning beyond the accepted one.
set -u
cd "$(dirname "$0")/../.." || exit 2

ACCEPTED='CLAUDE.md at the plugin root is not loaded as project context'

out=$(claude plugin validate . 2>&1); rc=$?
printf '%s\n' "$out"
[ "$rc" -eq 0 ] || exit "$rc"

fail() {
  echo "plugin validate: FAILED — $1" >&2
  # the count line and every indented bullet, minus the accepted one, so the unexpected warning is named
  printf '%s\n' "$out" | grep -E -i 'warning|^  [^ ]' | grep -F -v "$ACCEPTED" | sed 's/^/  /' >&2
  exit 1
}

if printf '%s\n' "$out" | grep -qi 'warning'; then
  count=$(printf '%s\n' "$out" | sed -n 's/.*Found \([0-9][0-9]*\) warning.*/\1/p' | head -n 1)
  [ -n "$count" ] || fail "the validator mentions warnings but prints no 'Found N warning' count this script can read (update bin/plugin_validate.sh):"
  if [ "$count" -ne 1 ] || ! printf '%s\n' "$out" | grep -qF "$ACCEPTED"; then
    fail "warning(s) beyond the accepted root CLAUDE.md one (treated as errors, like --strict):"
  fi
fi
claude plugin validate --strict skills >/dev/null || exit 1
claude plugin validate --strict agents >/dev/null || exit 1
echo "plugin validate: OK"
