# .context DB engine — Claude's CLI for the context document DB.
# The engine (this Makefile + bin/ + _templates/) lives under .claude/context-db/ so
# .claude/ is a portable kit; the CONTENT it operates on lives in a separate .context/
# folder (default: the sibling ../../.context, override with CONTEXT=).
# Each *.md doc carries YAML frontmatter (its "row"); INDEX.md is the generated catalog.
# These targets create, index, verify, and find docs.
#
#   make -C .claude/context-db new TYPE=epic DOMAIN=<domain> SLUG=key-123-foo TITLE="Foo epic"
#   make -C .claude/context-db index          # regenerate INDEX.md (run after any change)
#   make -C .claude/context-db verify         # validate frontmatter + INDEX freshness (CI-style gate)
#   make -C .claude/context-db find TAG=pii            # docs carrying a tag
#   make -C .claude/context-db find DOMAIN=<domain>    # docs in a domain
#   make -C .claude/context-db archive SLUG=opine      # flip a doc to status: archived
#
# Live session registry (who is working on what, right now — see SESSION_INDEX.md):
#   make -C .claude/context-db session-register NAME=<name> EPIC=KEY-123 REPOS=<repo> \
#                         WORKING="KEY-456 go-live" RESP="<what this session owns>" REF=<ref>
#   make -C .claude/context-db session-touch NAME=<name>  # <=12h keep-alive heartbeat (also on every flush)
#   make -C .claude/context-db session-end NAME=<name>    # mark ended; writes ## Session stats + sessions/_ledger.md row
#   make -C .claude/context-db session-stats             # print this session's stats block (turns, ctx, tokens, ~$, PRs…)
#   make -C .claude/context-db session-index             # regenerate SESSION_INDEX.md (MAX_ENDED=5 rows of ended sessions)
#   make -C .claude/context-db session-archive           # sweep ended sessions (> ARCHIVE_DAYS=7 old, or no next prompt for NOPROMPT_HOURS=48) to sessions/archive/; DRY=1 lists only
#   Every session-* target refreshes the row's `stats:` line from the transcript named by
#   $CLAUDE_CODE_SESSION_ID (or SESSION_ID=<uuid>); NOSTATS=1 skips it. Zero model turns.
#
# Kit / config (env fact store .context/reference/env/):
#   make -C .claude/context-db kb ARGS="get slack.channel help"       # kb.py: get|set|rm|list|values|config|init
#   make -C .claude/context-db kb ARGS="set slack.channel foo C0… --from tool:slack_search_channels"
#   make -C .claude/context-db profile                   # active environment name
#   make -C .claude/context-db profile KEY=tracker.kind  # one env-config value
#   make -C .claude/context-db kit-verify [STALE=90]     # every skill/agent carries versioning frontmatter; env store complete
#   make -C .claude/context-db verify-skill [UNIT=skills/x] # env-free validator: frontmatter schema, body, leak shapes — no .context/ needed
#   make -C .claude/context-db ci                        # exactly what .github/workflows/ci.yml runs, minus the blank env store
#   make -C .claude/context-db check-links               # every relative Markdown link resolves
#   make -C .claude/context-db install-smoke             # README's clone + plugin install blocks on a scratch HOME (plugin needs `claude`)
#   make -C .claude/context-db shellcheck                # shellcheck -S warning over the shell scripts and hooks
#   make -C .claude/context-db review-gate [BASE=origin/main] [SKIP_BUMP=1]  # tier 0 of the review: leak shapes on added lines, bumps
#   make -C .claude/context-db review-gate-tree                                # leak shapes over EVERY tracked file (#95), not the diff
#   make -C .claude/context-db review-evidence [BASE=origin/main]           # the evidence file the reviewer reads (.review/evidence.md)
#   make -C .claude/context-db test                      # the engine's stdlib unittest suite (context-db/tests/)
#   make -C .claude/context-db eval-check                # static check of evals/ (no tokens): case format, trigger suites
#   make -C .claude/context-db eval [SKILL=pr-open] [MODEL=<id>] [RUNS=3]  # run the eval suite (SPENDS TOKENS)
#   make -C .claude/context-db sync-check                # warn when .claude/ is ahead of origin or the last sync errored
#   make -C .claude/context-db kit-health [STALE=90]     # full kit audit for this machine (the kit-health skill's script)
#   make -C .claude/context-db engine-cli-doc           # regenerate docs/engine-cli.md from this help + every tool's --help (tests fail on drift)
#
# Content root: CONTEXT defaults to ../../.context (the sibling of .claude/). Point the
# engine at a different content DB with, e.g.:
#   make -C .claude/context-db index CONTEXT=/path/to/some/.context
# Always run `index` after adding or editing a doc's frontmatter.

ENGINE  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BIN     := $(ENGINE)bin
# kit_profile.py context = CONTEXT_ROOT, else the first of ../../.context, ../../../.context that exists
# (so the engine also works from a kit worktree under <root>/.worktrees/<name>/).
PY      := python3
# Evaluated once, not per reference. No inline comment here: make keeps the blanks before a `#`
# in the value, and `.context  ` is not the store.
ifeq ($(origin CONTEXT),undefined)
CONTEXT := $(shell $(PY) $(BIN)/kit_profile.py context)
endif
# Ended sessions shown as rows in SESSION_INDEX.md (older ones by name only): make … MAX_ENDED=<n>,
# or SESSION_INDEX_MAX_ENDED in the shell environment as the durable knob.
ifeq ($(origin MAX_ENDED),undefined)
MAX_ENDED := $(or $(SESSION_INDEX_MAX_ENDED),5)
endif
export SESSION_INDEX_MAX_ENDED := $(MAX_ENDED)
# Ended sessions leave SESSION_INDEX.md for sessions/archive/ when they ended more than ARCHIVE_DAYS ago, or
# carry no next-session prompt for NOPROMPT_HOURS (a crashed session is ended without one and may be resumed;
# the window is capped at ARCHIVE_DAYS, so ARCHIVE_DAYS=1 also shrinks it to 24 h). Every session-index run
# sweeps: make … ARCHIVE_DAYS=<n> (0 = all ended) NOPROMPT_HOURS=<h>, or SESSION_ARCHIVE_DAYS /
# SESSION_ARCHIVE_NOPROMPT_HOURS in the shell environment as the durable knobs. session-register / session-touch /
# session-end move an archived file back first, so the window never breaks a resumed session's hand-off.
ifeq ($(origin ARCHIVE_DAYS),undefined)
ARCHIVE_DAYS := $(or $(SESSION_ARCHIVE_DAYS),7)
endif
export SESSION_ARCHIVE_DAYS := $(ARCHIVE_DAYS)
ifeq ($(origin NOPROMPT_HOURS),undefined)
NOPROMPT_HOURS := $(or $(SESSION_ARCHIVE_NOPROMPT_HOURS),48)
endif
export SESSION_ARCHIVE_NOPROMPT_HOURS := $(NOPROMPT_HOURS)

.PHONY: help new index verify find archive test eval eval-check session-register session-touch session-end session-stats session-index session-archive kit-verify verify-skill ci profile kb sync-check kit-health review-gate review-gate-tree review-evidence plugin-validate check-links shellcheck engine-cli-doc install-smoke
SESSION_ID ?= $(CLAUDE_CODE_SESSION_ID)
STATSFLAGS  = $(if $(SESSION_ID),--session-id "$(SESSION_ID)",) $(if $(NOSTATS),--no-stats,)

help:
	@sed -n '/^[^#]/q;p' $(lastword $(MAKEFILE_LIST))  # the comment header, however long it grows

# make profile [KEY=dotted.key]  — the active environment, or one env-config value
profile:
	@$(PY) $(BIN)/kit_profile.py $(if $(KEY),get $(KEY),name)

# make kb ARGS="…"  — the env fact store CLI (python3 bin/kb.py …)
kb:
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/kb.py $(ARGS)

# make sync-check  — non-fatal: is the kit repo in step with origin? (session-register runs it)
sync-check:
	@sh $(ENGINE)../sync-check.sh || true

kit-health:
	@$(PY) $(ENGINE)../skills/kit-health/kit-health.py --stale $(or $(STALE),90)

# make engine-cli-doc  — docs/engine-cli.md, generated from `make help` + every engine tool's --help;
# tests/test_engine_cli_doc.py fails CI when the committed doc drifts from this output.
engine-cli-doc:
	@$(PY) $(BIN)/gen_engine_cli.py

new:
	@dest=$$(CONTEXT_ROOT="$(CONTEXT)" TYPE="$(TYPE)" DOMAIN="$(DOMAIN)" SLUG="$(SLUG)" TITLE="$(TITLE)" sh $(BIN)/new.sh) && \
		echo "created $$dest" && \
		$(MAKE) --no-print-directory -f $(lastword $(MAKEFILE_LIST)) index

index:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) gen_index.py

verify:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) verify.py

# make test  — the engine's stdlib unittest suite (context-db/tests/). A clone without an env store (a contributor's
# checkout, `make ci` before setup) gets a throw-away blank one, so the suite never needs this machine's .context/.
# make test [ISOLATED=1]  — ISOLATED=1 always runs on a throw-away blank store, never the workspace's own: ci.yml's
# decoy gate (#95) seeds the sibling .context/ with markers and fails if one reaches the output
test:
	@ctx="$(CONTEXT)"; tmp=""; if [ -n "$(ISOLATED)" ] || [ ! -f "$$ctx/reference/env/config.json" ]; then \
	  tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; ctx="$$tmp/.context"; CONTEXT_ROOT="$$ctx" $(PY) $(BIN)/kb.py init --blank >/dev/null; \
	  echo "test: $(if $(ISOLATED),ISOLATED=1,no env store at $(CONTEXT)) — using a throw-away blank one"; fi; \
	cd $(BIN)/.. && CONTEXT_ROOT="$$ctx" $(PY) -m unittest discover -s tests -t . -v

# make kit-verify [STALE=90]  — every skill/agent carries the versioning frontmatter
kit-verify:
	@cd $(BIN) && $(PY) kit_verify.py $(if $(STALE),--stale $(STALE),)

# make verify-skill [UNIT=skills/<name>|agents/<name>.md …]  — the environment-free validator: the same
# frontmatter, body and leak-shape checks as kit-verify, no env store or .context/ needed, so a contributor
# runs it on a bare clone. UNIT (not PATH: a make variable named PATH would shadow the shell's) is relative
# to the kit root; without it every unit is checked.
verify-skill:
	@cd $(ENGINE).. && $(PY) context-db/bin/kit_verify.py --no-env $(UNIT)

# make ci  — what .github/workflows/ci.yml runs, reproducible on any clone: the env-free validator, the engine
# tests, every Python file compiles, every shell script parses. (CI additionally runs kit-verify against a blank
# env store — `python3 context-db/bin/kb.py init --blank` from the workspace root gives you one.)
ci: verify-skill review-gate-tree test eval-check plugin-validate check-links shellcheck
	@cd $(ENGINE).. && git ls-files -z '*.py' | xargs -0 -r $(PY) -m py_compile && echo "py_compile: OK"
	@cd $(ENGINE).. && git ls-files -z '*.sh' hooks/ ':!hooks/*.json' | xargs -0 -n1 bash -n && echo "bash -n: OK"
	@cd $(ENGINE).. && sh_files="$$(git ls-files '*.sh' | while read -r f; do head -1 "$$f" | grep -q '^#!/bin/sh' && echo "$$f"; done)"; \
	  for f in $$sh_files; do if command -v dash >/dev/null 2>&1; then dash -n "$$f"; else sh -n "$$f"; fi || exit 1; done; echo "sh -n (dash): OK"

# make eval-check [REQUIRE_ALL=1]  — the eval suite's static gate (#54; no model, no tokens): every case loads
# (prompt.md keys, graders of a known type), every trigger suite has >= 10 cases with positives and near misses.
# Skills without a suite and descriptions without "Use when" are notes; REQUIRE_ALL=1 turns them into failures.
eval-check:
	@cd $(ENGINE).. && $(PY) $(BIN)/eval_check.py $(if $(REQUIRE_ALL),--require-all,)

# make eval [SKILL=<name>] [CASE=<glob>] [MODEL=<id>] [RUNS=<n>] [ABLATION=none|with-without] [THRESHOLD=<0..1>]
#           [MAX_COST=<usd>] [TRUST=1]  — `claude plugin eval` on this plugin. It SPENDS TOKENS on your credential; CI
# runs it only through the manual `evals` workflow. SKILL narrows to evals/<skill>-*; ABLATION=none (the default here)
# scores every grader, which is what a trigger suite measures. Results land in evals/results/ (git-ignored) and the
# report stays local (--no-publish). TRUST=1 answers the first-run trust prompt (CI only).
ABLATION ?= none
eval:
	@command -v claude >/dev/null 2>&1 || { echo "eval: needs the claude CLI"; exit 2; }
	@cd $(ENGINE).. && claude plugin eval . --no-publish --ablation $(ABLATION) \
	  $(if $(CASE),--case '$(CASE)',$(if $(SKILL),--case '$(SKILL)-*',)) \
	  $(if $(MODEL),--model '$(MODEL)',) $(if $(RUNS),--runs $(RUNS),) $(if $(THRESHOLD),--threshold $(THRESHOLD),) \
	  $(if $(MAX_COST),--max-cost-usd $(MAX_COST),) $(if $(TRUST),--trust-plugin,)

# The relative Markdown links of every tracked *.md resolve; templates seeded elsewhere are skipped.
check-links:
	@cd $(ENGINE).. && $(PY) context-db/bin/check_links.py

# The README's install blocks replayed on a scratch HOME (#98): the clone path, then the plugin path (skipped without
# the `claude` CLI). The same script is CI's install-smoke job.
install-smoke:
	@cd $(ENGINE).. && $(PY) context-db/tests/install_smoke.py clone && $(PY) context-db/tests/install_smoke.py plugin

# shellcheck -S warning over every tracked shell script and the git hooks: the system binary, else the
# PyPI wheel through uvx; a machine with neither says so (CI has uv).
shellcheck:
	@cd $(ENGINE).. && files="$$(git ls-files '*.sh' | grep -v /fixtures/) hooks/pre-push hooks/commit-msg"; \
	  if command -v shellcheck >/dev/null 2>&1; then shellcheck -S warning $$files && echo "shellcheck: OK"; \
	  elif command -v uvx >/dev/null 2>&1; then uvx --from shellcheck-py shellcheck -S warning $$files && echo "shellcheck: OK (uvx)"; \
	  else echo "shellcheck: skipped (neither shellcheck nor uvx on this machine)"; fi

# make plugin-validate  — the plugin and marketplace manifests + every skill/agent, through Claude Code's own validator
# (`claude plugin validate --strict`, docs/packaging.md). A clone without the `claude` CLI says so instead of failing:
# kit-verify still checks the version discipline (plugin.json version == VERSION) on every machine.
plugin-validate:
	@cd $(ENGINE).. && if command -v claude >/dev/null 2>&1; then claude plugin validate --strict . && claude plugin validate --strict skills >/dev/null && claude plugin validate --strict agents >/dev/null && echo "plugin validate: OK"; \
	  else echo "plugin validate: skipped (no \`claude\` CLI on this machine)"; fi

# Tier 0 and tier 1 of the review (docs/REVIEW.md): the gate fails on a leak shape in an added line or a changed
# unit without version/CHANGELOG bump (SKIP_BUMP=1 for a wording-only change); the evidence file is what the CI
# reviewer reads (.review/ is git-ignored).
BASE ?= origin/main
review-gate:
	@cd $(ENGINE).. && $(PY) $(BIN)/review_gate.py --base $(BASE) --head HEAD $(if $(SKIP_BUMP),--skip-bump,)
review-gate-tree:
	@cd $(ENGINE).. && $(PY) $(BIN)/review_gate.py --tree --head HEAD
review-evidence:
	@cd $(ENGINE).. && $(PY) $(BIN)/review_evidence.py --base $(BASE) --head HEAD $(if $(SKIP_BUMP),--skip-bump,) --out .review/evidence.md
# make find TAG=foo   |   make find DOMAIN=bar
find:
ifdef TAG
	@grep -rIl --include='*.md' -E '^tags:.*\b$(TAG)\b' $(CONTEXT) | sed 's|$(CONTEXT)/||' | sort || true
endif
ifdef DOMAIN
	@grep -rIl --include='*.md' -E '^domain:[[:space:]]*$(DOMAIN)[[:space:]]*$$' $(CONTEXT) | sed 's|$(CONTEXT)/||' | sort || true
endif
ifeq ($(TAG)$(DOMAIN),)
	@echo "usage: make find TAG=<tag>  |  make find DOMAIN=<domain>"
endif

# --- live session registry (operational; skipped by index/verify) ---
# Each session writes ONLY its own sessions/<NAME>.md, so sessions never collide.
session-register:
	@test -n "$(NAME)" || { echo "usage: make session-register NAME=<session> [EPIC=] [REPOS=] [WORKING=] [RESP=] [REF=] [STATUS=]"; exit 2; }
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py register --name "$(NAME)" --ref "$(REF)" --status "$(STATUS)" --epic "$(EPIC)" --repos "$(REPOS)" --working "$(WORKING)" --resp "$(RESP)" --note "$(NOTE)" $(STATSFLAGS)
	@sh $(ENGINE)../sync-check.sh || true

session-touch:
	@test -n "$(NAME)" || { echo "usage: make session-touch NAME=<session> [WORKING=] [STATUS=]"; exit 2; }
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py touch --name "$(NAME)" --status "$(STATUS)" --working "$(WORKING)" $(if $(NEXT),--next "$(NEXT)") $(STATSFLAGS)

session-end:
	@test -n "$(NAME)" || { echo "usage: make session-end NAME=<session> [NEXT=<file with the next-session prompt>] [WORKING=]"; exit 2; }
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py end --name "$(NAME)" --working "$(WORKING)" $(if $(NEXT),--next "$(NEXT)") $(STATSFLAGS)

# make session-stats [SESSION_ID=<uuid>] — the stats block for the handoff (no registry write)
session-stats:
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py stats $(STATSFLAGS)

session-index:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) gen_sessions.py $(if $(NOARCHIVE),--no-archive,)

# make session-archive [ARCHIVE_DAYS=<n>] [NOPROMPT_HOURS=<h>] [DRY=1] — same sweep session-index runs, on its own;
# DRY=1 only lists. NOARCHIVE=1 (here or on session-index) regenerates without sweeping.
session-archive:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) gen_sessions.py $(if $(DRY),--dry-run,) $(if $(NOARCHIVE),--no-archive,)

# make archive SLUG=<file-slug>  — flip status to archived in place (kept in INDEX).
archive:
	@test -n "$(SLUG)" || { echo "usage: make archive SLUG=<file-slug>"; exit 2; }
	@f=$$(find $(CONTEXT) -name '$(SLUG).md' -not -path '*/_templates/*' | head -1); \
	 test -n "$$f" || { echo "no doc named $(SLUG).md"; exit 1; }; \
	 sed -i.bak -E 's|^status:.*|status: archived|' "$$f" && rm -f "$$f.bak" && \
	 echo "archived $$f (still indexed; move to archive/ by hand if you want it out of the domain view)"; \
	 $(MAKE) --no-print-directory -f $(lastword $(MAKEFILE_LIST)) index
