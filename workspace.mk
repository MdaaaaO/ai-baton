# workspace.mk — shared Make targets for a Data Engineering Claude workspace.
# Include it from the root Makefile:   include .claude/workspace.mk
# Everything here is user-agnostic; keep personal/host-specific targets in the root Makefile.

# ── sign queue (host side) ───────────────────────────────────────────────────────────────
# Sessions enqueue signed-commit jobs under .context/state/sign-queue/ (skill `sign-queue`); the user
# drains them here with signq.py. Runs on the HOST (signing + SSH push need the host keys), in
# enqueue order, fully non-interactive (no pager, no editor): an overview table (ticket / epic /
# repo / PR / commit) first, then one card per job with the milestones and a ✔/✘ line, then a
# summary with the PR links. A job is deleted on success, parked as <job>.failed on failure (the
# owning session re-enqueues; `sign_retry` re-runs it as is). Per-job output:
# .context/state/sign-queue/logs/<job>.log.
#   make sign                  # drain every pending job        (make sign V=1 streams all git output)
#   make sign_list             # overview of pending + parked jobs, runs nothing
#   make sign_show JOB=1       # metadata + script of one job (index from sign_list, or topic)
#   make sign_log  JOB=1       # last drain log of one job
#   make sign_retry JOB=1      # un-park a failed job for the next drain
#   make sign_drop  JOB=1      # delete a pending/parked job
_SIGNQ := python3 .claude/skills/sign-queue/signq.py
JOB ?=
V   ?=

sign:
	@$(_SIGNQ) run $(if $(V),-v,)

sign_list:
	@$(_SIGNQ) list

sign_show sign_log sign_retry sign_drop:
	@test -n "$(JOB)" || { echo "usage: make $@ JOB=<index|topic>  (see make sign_list)"; exit 1; }
	@$(_SIGNQ) $(patsubst sign_%,%,$@) "$(JOB)"

# ── .claude workspace repo sync ──────────────────────────────────────────────────────────
# .claude/ is its own git repo (see .claude/docs/sync.md) and its main is PR-only: sync.sh
# installs the hooks/pre-push guard and fast-forwards .claude/ to origin/main (refuses, with an
# `error` in .sync-status, when .claude/ is dirty, off main or ahead — move that work to a branch
# + PR). Nothing is committed or pushed by it. Lock-guarded, never fails, logs to .claude/sync.log;
# the target prints the log's tail and .sync-status (pending/ok/offline/error, .claude/docs/sync.md).
# A SessionEnd hook in .claude/settings.json runs it too.
#   make claude_sync                    # kit: pull (ff-only)
claude_sync:
	@sh .claude/sync.sh
	@tail -n 3 .claude/sync.log 2>/dev/null || true
	@cat .claude/.sync-status 2>/dev/null || true

# ── kit releases (conventional-release) ──────────────────────────────────────────────────
# A kit release = a CHANGELOG.md section + the VERSION bump, as a `chore(release): X.Y.Z` PR; CI tags the
# squash-merged commit vX.Y.Z and publishes the GitHub Release (docs/contributing.md § Releases).
# The kit checkout (KIT_CHECKOUT) stays on its branch, so the release branch is cut in a throwaway worktree off
# origin/main. KIT_CHECKOUT defaults to the workspace's .claude/ clone; a plugin install has none (the plugin cache
# is not a git checkout), so name a clone of the kit repo and include this file by path (#85):
#   make -f $BATON/workspace.mk kit_release_dry KIT_CHECKOUT=<kit clone>
# Needs uv (uvx) and gh; the PR gets the `release` label (a failed label fails the target). A run that fails
# after cutting the branch keeps the branch and the worktree — the release commit may be only there — and
# prints the push/PR commands that finish it.
#   make kit_release_dry               # the next version and its changelog section (as of origin/main), nothing written
#   make kit_release [LEVEL=minor]     # branch, commit, push, PR (LEVEL: major|minor|patch|X.Y.Z; default inferred)
_CREL    := uvx -q --from 'conventional-release>=0.2,<1' conventional-release
_REL_WT  := .worktrees/kit_release-run
LEVEL    ?=
KIT_CHECKOUT ?= $(if $(wildcard .claude/.git),.claude,)
_CTXROOT := $(if $(wildcard .context/reference/env/config.json),CONTEXT_ROOT=$(CURDIR)/.context,)

# both run in a detached worktree off origin/main, so the dry run shows exactly what kit_release would cut
kit_release_dry kit_release:
	@git -C "$(or $(KIT_CHECKOUT),.)" rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ -n "$(KIT_CHECKOUT)" ] || { echo "kit_release: no kit git checkout ('$(KIT_CHECKOUT)') — pass KIT_CHECKOUT=<a clone of the kit repo>; a plugin install has no clone of its own"; exit 2; }
	@git -C $(KIT_CHECKOUT) fetch -q --tags origin
	@test ! -e $(_REL_WT) || { echo "$(_REL_WT) exists — a release run in progress, or left over: git -C $(KIT_CHECKOUT) worktree remove --force $(CURDIR)/$(_REL_WT)"; exit 1; }
	@git -C $(KIT_CHECKOUT) worktree add -q --detach $(CURDIR)/$(_REL_WT) origin/main
	@eval "$$($(_CTXROOT) python3 $(_REL_WT)/context-db/bin/kit_profile.py gh-env)"; \
	  out="$$(cd $(_REL_WT) && $(_CREL) release $(if $(filter kit_release_dry,$@),--dry-run) $(LEVEL))"; rc=$$?; \
	  br="$$(git -C $(_REL_WT) branch --show-current)"; \
	  printf '%s\n' "$$out"; \
	  if [ $$rc -ne 0 ] && [ -n "$$br" ]; then \
	    echo "kit_release: failed (exit $$rc) after cutting $$br — kept it and its worktree, which may hold the release commit:"; \
	    echo "  worktree  $(CURDIR)/$(_REL_WT)"; \
	    echo "  branch    $$br"; \
	    echo "finish it (skip what already happened — the output above says how far it got):"; \
	    echo "  cd $(CURDIR)/$(_REL_WT) && git push -u origin $$br && gh pr create --base main --head $$br --fill --label release"; \
	    echo "then clean up:"; \
	    echo "  git -C $(KIT_CHECKOUT) worktree remove $(CURDIR)/$(_REL_WT) && git -C $(KIT_CHECKOUT) branch -D $$br"; \
	    exit $$rc; \
	  fi; \
	  git -C $(KIT_CHECKOUT) worktree remove --force $(CURDIR)/$(_REL_WT); \
	  if [ -n "$$br" ]; then git -C $(KIT_CHECKOUT) branch -q -D "$$br"; fi; \
	  [ $$rc -eq 0 ] || exit $$rc; \
	  $(if $(filter kit_release_dry,$@),exit 0;) \
	  url="$$(printf '%s\n' "$$out" | grep -o 'https://github.com/[^ ]*/pull/[0-9]*' | tail -n 1)"; \
	  if [ -z "$$url" ]; then echo "kit_release: no PR URL in the output above — add the release label by hand: gh pr edit <url> --add-label release"; exit 1; fi; \
	  gh pr edit "$$url" --add-label release >/dev/null || { echo "kit_release: PR $$url is open, but labelling it failed — gh pr edit $$url --add-label release"; exit 1; }; \
	  echo "labelled: release"

# ── .context document DB shorthand ───────────────────────────────────────────────────────
# The engine lives in .claude/context-db (make -C .claude/context-db <target>); these are aliases.
#   make ctx_index / ctx_verify / ctx_find DOMAIN=<domain> / ctx_find TAG=pii
ctx_index ctx_verify:
	@$(MAKE) -C .claude/context-db $(patsubst ctx_%,%,$@)

ctx_find:
	@$(MAKE) -C .claude/context-db find $(if $(DOMAIN),DOMAIN=$(DOMAIN),) $(if $(TAG),TAG=$(TAG),)

.PHONY: sign sign_list sign_show sign_log sign_retry sign_drop claude_sync kit_release kit_release_dry ctx_index ctx_verify ctx_find
