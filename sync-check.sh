#!/bin/sh
# shellcheck shell=dash  # run as `sh` everywhere (README, workspace.mk); dash has `local`
# sync-check.sh — is the .claude kit in step with origin?
# Non-fatal, a few lines on stderr. Run by `make -C .claude/context-db session-register` (and
# `sync-check`), so every session sees at registration whether the previous session's background
# sync silently failed, whether local commits on main can never leave this machine (main is
# PR-only), or whether origin has moved on (a release tag is waiting; on `kit.channel main`, a PR
# merged). Exit 0 always; the WARN lines are the signal —
# silence means "in step". So every state that is NOT "in step" says so: not a git checkout (outside a plugin install,
# which kit-health reports itself), no origin/main to compare with, or a git call that failed.
#
#   sh .claude/sync-check.sh            # prints nothing when everything is in step
OFFLINE_WARN_DAYS=3      # an offline laptop is normal; warn once no fetch has reached origin for this long
PENDING_STALE_SECS=300   # a sync's fetch is bounded at 60 s (sync.sh); `pending` older than this = the run was killed

HERE="$(cd "$(dirname "$0")" && pwd)"
warn() { printf 'WARN kit sync: %s\n' "$*" >&2; }
is_epoch() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; }

if [ -f "$HERE/.sync-status" ]; then
  # `<utc-ts> <state> <detail>` — states in sync.sh's header; `ok` and a fresh `pending` are silent
  read -r ts state detail <"$HERE/.sync-status"
  now=$(date -u +%s)
  first=${detail%% *}
  case "$state" in
    error) warn "last sync at $ts failed — $detail; run \`make claude_sync\` and read its tail" ;;
    pending)
      if is_epoch "$first" && [ $((now - first)) -ge "$PENDING_STALE_SECS" ]; then
        warn "the sync started at $ts never finished (killed mid-fetch, e.g. the terminal closed) — nothing is known about origin since; run \`make claude_sync\`"
      fi ;;
    offline)
      if is_epoch "$first" && [ $((now - first)) -ge $((OFFLINE_WARN_DAYS * 86400)) ]; then
        since=${detail#* since }
        warn "origin unreachable for $(((now - first) / 86400)) day(s), since ${since%% *} — offline, or a DNS/proxy problem? run \`make claude_sync\` once online and read its tail"
      fi ;;
  esac
fi

# check_kit — the kit's main is never pushed from here: warn on local commits, on being behind
# (the release channel: the highest release tag origin/main contains is not in HEAD yet — commits
# nobody released are not "behind"; `kit.channel main`, or no release tag yet: origin/main itself),
# on a non-main checkout, a missing pre-push guard, and uncommitted changes.
check_kit() {
  local dir=$1 label=kit
  cd "$dir" || { warn "$label: cannot enter $dir — sync state unknown"; return 0; }
  # a worktree or submodule has a .git FILE, so ask git, not the filesystem
  if [ "$(git rev-parse --is-inside-work-tree 2>/dev/null)" != true ]; then
    mode=$(python3 "$dir/context-db/bin/kit_profile.py" install-mode 2>/dev/null)
    # a plugin install has no checkout by design, and `.claude` without git is the copy sync.sh itself
    # skips (docs/packaging.md) — both are kit-health's to report, not this script's; anything else
    # (a broken/foreign directory) is a state worth flagging here
    case "$mode" in
      plugin|clone) ;;
      *) warn "$label: $dir is not a git checkout (install mode '${mode:-unknown}') — sync state unknown; re-clone or re-run setup.sh" ;;
    esac
    return 0
  fi
  if ! git remote get-url origin >/dev/null 2>&1; then
    warn "$label: no \`origin\` remote — nothing to compare with; \`git remote add origin <kit repo>\` and \`make claude_sync\`"
  elif ! git rev-parse --verify -q origin/main >/dev/null; then
    warn "$label: no origin/main ref (never fetched?) — \`make claude_sync\` fetches it"
  elif ! ahead=$(git rev-list --count origin/main..HEAD 2>&1); then
    warn "$label: could not compare HEAD with origin/main (git: $ahead) — sync state unknown"
  elif ! behind=$(git rev-list --count HEAD..origin/main 2>&1); then
    warn "$label: could not compare HEAD with origin/main (git: $behind) — sync state unknown"
  else
    [ "$ahead" -gt 0 ] && warn "$label: $ahead local commit(s) on main that will never be pushed — main is PR-only: \`git branch <topic> && git reset --hard origin/main\`, open a PR from <topic>"
    # the same channel and tag choice as sync.sh
    tag=""
    if [ "$(git config --get kit.channel 2>/dev/null || true)" != main ]; then
      tag="$(git for-each-ref --merged origin/main --sort=-version:refname --format='%(refname:short)' 'refs/tags/v[0-9]*' 2>/dev/null | head -n 1)"
    fi
    if [ -n "$tag" ]; then
      if ! git merge-base --is-ancestor "refs/tags/$tag" HEAD 2>/dev/null; then
        # a tag --accept already rejected is reported once, above, straight from .sync-status
        # (`error …`) — sync.sh writes no .sync-preview for it, so this second warning would be
        # both redundant and misleading about what `make claude_sync` will actually do
        rejtag=""
        [ -f "$dir/.sync-rejected" ] && rejtag="$(cut -d' ' -f1 "$dir/.sync-rejected" 2>/dev/null)"
        [ "$rejtag" = "$tag" ] \
          || warn "$label: release $tag is waiting (what it changes: .sync-preview) — \`make claude_sync\` applies it"
      fi
    elif [ "$behind" -gt 0 ]; then
      warn "$label: origin/main is $behind commit(s) ahead (a PR merged) — \`make claude_sync\` fast-forwards"
    fi
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
