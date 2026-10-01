# workspace.mk — shared Make targets for an ai-baton workspace.
# Include it from the root Makefile:   include .claude/workspace.mk
# Everything here is user-agnostic; keep personal/host-specific targets in the root Makefile.
# Every target names the kit as $(KIT): the directory this file was included from (`.claude` on a clone, the
# plugin root or a checkout under any other name when included by path); `make … KIT=<dir>` overrides (an environment
# variable does not). Recipe paths are
# double-quoted, so a workspace path with a space stays one argument.
_WORKSPACE_MK := $(lastword $(MAKEFILE_LIST))
KIT := $(patsubst %/,%,$(dir $(_WORKSPACE_MK)))

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
_SIGNQ = python3 "$(KIT)/skills/sign-queue/signq.py"
JOB ?=
V   ?=
# gate text only — never a $(shell ...) Make variable: that would run kit_profile.py eagerly at parse time,
# including under `make -n`. As plain shell text pasted into one recipe line below, `make -n` only prints
# it; nothing runs until the recipe's own shell does. Where systems.signed_commits is false (a solo
# workspace with no signing key) every sign* target prints one line and exits 0, never touching signq.py.
# kit_profile.py exits 1 for "no env store / key absent" (kit default: false) and 2 or more when the store
# could not be read — that one stops with the error, it is never read as "false".
_SIGN_GATE_SC = sge="$$(mktemp)"; sc="$$(python3 "$(KIT)/context-db/bin/kit_profile.py" get systems.signed_commits 2>"$$sge")"; sgrc=$$?; sgerr="$$(cat "$$sge")"; rm -f "$$sge"; if [ "$$sgrc" -ge 2 ]; then echo "sign-queue: systems.signed_commits could not be read — $$sgerr" >&2; exit 2; fi
_SIGN_NA = echo "sign-queue: not applicable here — signed_commits is false"

# the owner's entry points move jobs a pre-workspace-queue kit left under the kit dir first (a no-op once done);
# signq.py itself never migrates as a side effect
sign:
	@$(_SIGN_GATE_SC); if [ "$$sc" != "true" ]; then $(_SIGN_NA); else $(_SIGNQ) migrate-legacy -q && $(_SIGNQ) run $(if $(V),-v,); fi

sign_list:
	@$(_SIGN_GATE_SC); if [ "$$sc" != "true" ]; then $(_SIGN_NA); else $(_SIGNQ) migrate-legacy -q && $(_SIGNQ) list; fi

sign_show sign_log sign_retry sign_drop:
	@$(_SIGN_GATE_SC); if [ "$$sc" != "true" ]; then $(_SIGN_NA); elif [ -z "$(JOB)" ]; then echo "usage: make $@ JOB=<index|topic>  (see make sign_list)"; exit 1; else $(_SIGNQ) $(patsubst sign_%,%,$@) "$(JOB)"; fi

# ── .claude workspace repo sync ──────────────────────────────────────────────────────────
# .claude/ is its own git repo (see .claude/docs/sync.md) and its main is PR-only: sync.sh
# installs the hooks/pre-push guard and fast-forwards .claude/ to the highest release tag
# origin/main contains (refuses, with an `error` in .sync-status, when .claude/ is dirty, off main
# or ahead — move that work to a branch + PR). Nothing is committed or pushed by it. `--accept`
# applies a held release tag (a bare run only previews it, docs/sync.md) — the SessionEnd hook
# below never passes it, so an unattended sync can only hold, never apply. Lock-guarded (a busy
# lock exits 3 and names the holder), logs to $(KIT)/sync.log; the target prints the log's tail
# and .sync-status (pending/ok/held/offline/error, .claude/docs/sync.md).
# A SessionEnd hook in .claude/settings.json runs sync.sh too, without --accept.
#   make claude_sync                    # kit: pull (ff-only) and apply a held release tag
claude_sync:
	@sh "$(KIT)/sync.sh" --accept || [ $$? -eq 3 ]  # 3 = another sync holds the lock (it said who): not a failure here
	@tail -n 3 "$(KIT)/sync.log" 2>/dev/null || true
	@cat "$(KIT)/.sync-status" 2>/dev/null || true

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
# After conventional-release's own commit (it bumps VERSION and plugin.json — .conventional-release.toml
# version-files — but can't reach .claude-plugin/marketplace.json's plugins[].source.ref, a differently
# named, nested, tag-prefixed field its version-files mechanism has no path to), a real (non-dry) run pins that
# ref with `context-db/bin/bump_marketplace_ref.py` as a second commit on the same release branch — squashed
# into one `chore(release):` commit with everything else at merge, so an install still resolves the tagged
# release, not whatever main holds when it updates. A failed pin leaves the branch and worktree too.
#   make kit_release_dry               # the next version and its changelog section (as of origin/main), nothing written
#   make kit_release [LEVEL=minor]     # branch, commit, push, PR (LEVEL: major|minor|patch|X.Y.Z; default inferred)
# Below 1.0.0, an inferred major (a `BREAKING CHANGE:`/Machines footer) is cut as a minor instead — every machine
# takes that step before 1.0 — unless LEVEL names one explicitly (LEVEL=major still cuts 1.0.0). The check asks
# the tool itself (`next`, never its own reading of the commits); when the tool cannot name the next version,
# nothing is cut and the recipe says why, so an accidental 1.0.0 never rides on a failed check.
# conventional-release's own version is an exact pin, not a floating range — one pin, .github/versions.env, next to
# this file (the workspace's own kit checkout; #85's KIT_CHECKOUT names a release's git checkout when it differs
# from where workspace.mk itself lives, but the tool version tracks this file, like every other target here).
-include $(KIT)/.github/versions.env
# a test fakes this via `make _CREL=…` — a command-line assignment beats `?=` too, same as `:=` above
_CREL    ?= uvx -q --from conventional-release==$(CONVENTIONAL_RELEASE_VERSION) conventional-release
_REL_WT  := .worktrees/kit_release-run
LEVEL    ?=
# the default checkout: the workspace's own `.claude/` clone, and nothing else — a release never defaults to a kit that
# is not this workspace's (e.g. `make -f $BATON/workspace.mk` run from another directory); KIT_CHECKOUT=<dir> names one
KIT_CHECKOUT ?= $(if $(wildcard .claude/.git),.claude,)
_CTXROOT := $(if $(wildcard .context/reference/env/config.json),CONTEXT_ROOT="$(CURDIR)/.context",)
_REL_ABS  = $(CURDIR)/$(_REL_WT)

# both run in a detached worktree off origin/main, so the dry run shows exactly what kit_release would cut
kit_release_dry kit_release:
	@git -C "$(or $(KIT_CHECKOUT),.)" rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ -n "$(KIT_CHECKOUT)" ] || { echo "kit_release: no kit git checkout ('$(KIT_CHECKOUT)') — pass KIT_CHECKOUT=<a clone of the kit repo>; a plugin install has no clone of its own"; exit 2; }
	@git -C "$(KIT_CHECKOUT)" fetch -q --tags origin
	@test ! -e "$(_REL_WT)" || { echo "$(_REL_WT) exists — a release run in progress, or left over: git -C '$(KIT_CHECKOUT)' worktree remove --force '$(_REL_ABS)'"; exit 1; }
	@git -C "$(KIT_CHECKOUT)" worktree add -q --detach "$(_REL_ABS)" origin/main
	@eval "$$($(_CTXROOT) python3 "$(_REL_WT)/context-db/bin/kit_profile.py" gh-env)"; \
	  lvl="$(LEVEL)"; \
	  if [ -z "$$lvl" ] && [ "$$(cut -d. -f1 "$(_REL_WT)/VERSION" 2>/dev/null)" = "0" ]; then \
	    probe="$$(cd "$(_REL_WT)" && $(_CREL) next 2>&1)"; prc=$$?; \
	    next="$$(printf '%s\n' "$$probe" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+' | tail -n 1)"; \
	    if [ $$prc -ne 0 ] || [ -z "$$next" ]; then \
	      printf '%s\n' "$$probe"; \
	      echo "kit_release: the release tool did not name the next version (exit $$prc, output above) — nothing cut; fix that, or pass LEVEL=patch|minor|major"; \
	      git -C "$(KIT_CHECKOUT)" worktree remove --force "$(_REL_ABS)"; \
	      [ $$prc -ne 0 ] && exit $$prc; exit 1; \
	    fi; \
	    case "$$next" in 0.*) ;; *) \
	      lvl=minor; \
	      echo "kit_release: the commits infer $$next while the kit is 0.x — cutting a minor instead (pass LEVEL=major to cut 1.0.0)";; \
	    esac; \
	  fi; \
	  out="$$(cd "$(_REL_WT)" && $(_CREL) release $(if $(filter kit_release_dry,$@),--dry-run) $$lvl)"; rc=$$?; \
	  br="$$(git -C "$(_REL_WT)" branch --show-current)"; \
	  printf '%s\n' "$$out"; \
	  if [ $$rc -ne 0 ] && [ -n "$$br" ]; then \
	    echo "kit_release: failed (exit $$rc) after cutting $$br — kept it and its worktree, which may hold the release commit:"; \
	    echo "  worktree  $(_REL_ABS)"; \
	    echo "  branch    $$br"; \
	    echo "finish it (skip what already happened — the output above says how far it got):"; \
	    echo "  cd '$(_REL_ABS)' && git push -u origin $$br && gh pr create --base main --head $$br --fill --label release"; \
	    echo "then clean up:"; \
	    echo "  git -C '$(KIT_CHECKOUT)' worktree remove '$(_REL_ABS)' && git -C '$(KIT_CHECKOUT)' branch -D $$br"; \
	    exit $$rc; \
	  fi; \
	  if [ $$rc -eq 0 ] && [ -n "$$br" ]; then \
	    if ! ( cd "$(_REL_WT)" && python3 context-db/bin/bump_marketplace_ref.py \
	           && git add .claude-plugin/marketplace.json \
	           && { git diff --cached --quiet || git commit -q -m "chore(release): pin marketplace ref"; } \
	           && git push -q origin HEAD ); then \
	      echo "kit_release: pinning .claude-plugin/marketplace.json's ref failed on $$br — kept it and its worktree, the PR is open without the pin:"; \
	      echo "  worktree  $(_REL_ABS)"; \
	      echo "  branch    $$br"; \
	      echo "  cd '$(_REL_ABS)' && python3 context-db/bin/bump_marketplace_ref.py && git add .claude-plugin/marketplace.json && git commit -m 'chore(release): pin marketplace ref' && git push origin HEAD"; \
	      exit 1; \
	    fi; \
	  fi; \
	  git -C "$(KIT_CHECKOUT)" worktree remove --force "$(_REL_ABS)"; \
	  if [ -n "$$br" ]; then git -C "$(KIT_CHECKOUT)" branch -q -D "$$br"; fi; \
	  [ $$rc -eq 0 ] || exit $$rc; \
	  $(if $(filter kit_release_dry,$@),exit 0;) \
	  url="$$(printf '%s\n' "$$out" | grep -o 'https://github.com/[^ ]*/pull/[0-9]*' | tail -n 1)"; \
	  if [ -z "$$url" ]; then echo "kit_release: no PR URL in the output above — add the release label by hand: gh pr edit <url> --add-label release"; exit 1; fi; \
	  gh pr edit "$$url" --add-label release >/dev/null || { echo "kit_release: PR $$url is open, but labelling it failed — gh pr edit $$url --add-label release"; exit 1; }; \
	  echo "labelled: release"

# ── .context document DB shorthand ───────────────────────────────────────────────────────
# The engine lives in $(KIT)/context-db (make -C .claude/context-db <target> on a clone); these are aliases. The
# workspace's .context/ is passed as CONTEXT, so the engine finds it from a kit outside the workspace too.
#   make ctx_index / ctx_verify / ctx_find DOMAIN=<domain> / ctx_find TAG=pii
_CTXARG := $(if $(wildcard .context),CONTEXT="$(CURDIR)/.context",)
ctx_index ctx_verify:
	@$(MAKE) -C "$(KIT)/context-db" $(_CTXARG) $(patsubst ctx_%,%,$@)

ctx_find:
	@$(MAKE) -C "$(KIT)/context-db" $(_CTXARG) find $(if $(DOMAIN),DOMAIN=$(DOMAIN),) $(if $(TAG),TAG=$(TAG),)

.PHONY: sign sign_list sign_show sign_log sign_retry sign_drop claude_sync kit_release kit_release_dry ctx_index ctx_verify ctx_find
