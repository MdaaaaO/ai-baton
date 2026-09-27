#!/usr/bin/env bash
# sync-check.sh — is the .claude kit in step with origin?
# Non-fatal, a few lines on stderr. Run by `make -C .claude/context-db session-register` (and
# `sync-check`), so every session sees at registration whether the previous session's background
# sync silently failed, whether local commits on main can never leave this machine (main is
# PR-only), or whether origin has moved on (a PR merged). Exit 0 always; the WARN lines are the signal.
#
#   sh .claude/sync-check.sh            # prints nothing when everything is in step
HERE="$(cd "$(dirname "$0")" && pwd)"
warn() { printf 'WARN kit sync: %s\n' "$*" >&2; }

if [ -f "$HERE/.sync-status" ]; then
  # `<utc-ts> ok|error <detail>`
  read -r ts state detail <"$HERE/.sync-status"
  case "$state" in
    error) warn "last sync at $ts failed — $detail; run \`make claude_sync\` and read its tail" ;;
  esac
fi

# check_kit — the kit's main is never pushed from here: warn on local commits, on being behind
# origin/main, on a non-main checkout, a missing pre-push guard, and uncommitted changes.
check_kit() {
  local dir=$1 label=kit
  cd "$dir" || return 0
  [ -d .git ] || return 0
  if git remote get-url origin >/dev/null 2>&1 && git rev-parse --verify -q origin/main >/dev/null; then
    ahead=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
    behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
    [ "$ahead" -gt 0 ] && warn "$label: $ahead local commit(s) on main that will never be pushed — main is PR-only: \`git branch <topic> && git reset --hard origin/main\`, open a PR from <topic>"
    [ "$behind" -gt 0 ] && warn "$label: origin/main is $behind commit(s) ahead (a PR merged) — \`make claude_sync\` fast-forwards"
  fi
  branch="$(git symbolic-ref -q --short HEAD 2>/dev/null || echo DETACHED)"
  [ "$branch" != main ] && warn "$label: checked out on '$branch' — .claude/ must stay on main; branch work lives in a worktree under .worktrees/"
  [ "$(git config --get core.hooksPath 2>/dev/null)" = "$dir/hooks" ] || warn "$label: pre-push guard not installed — \`make claude_sync\` installs it (core.hooksPath)"
  if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    warn "$label: uncommitted changes in .claude/ — main is PR-only, nothing commits them: move them to a branch worktree and open a PR"
  fi
}

check_kit "$HERE"
# env store behind the kit's capability flags: kit-verify fails and re-gated skills read as off until migrated
rc=0; python3 "$HERE/context-db/bin/kb.py" migrate --check >/dev/null 2>&1 || rc=$?
[ "$rc" -ne 0 ] && [ "$rc" -ne 3 ] && warn "\`kb.py migrate --check\` failed (exit $rc) — no or unreadable env store? \`python3 \$BATON/context-db/bin/kb.py init --blank\` creates one"
[ "$rc" -eq 3 ] && warn "env store predates the kit's capability flags — \`python3 \$BATON/context-db/bin/kb.py migrate\` (keeps values, lists what it changed)"
exit 0
