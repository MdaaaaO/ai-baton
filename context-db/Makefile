# .context DB engine — Claude's CLI for the context document DB.
# The engine (this Makefile + bin/ + _templates/) lives under .claude/context-db/ so
# .claude/ is a portable kit; the CONTENT it operates on lives in a separate .context/
# folder (override with CONTEXT=; the default content root is docs/layout.md's "Content root" paragraph).
# Each *.md doc carries YAML frontmatter (its "row"); INDEX.md is the generated catalog.
# These targets create, index, verify, and find docs.
#
#   make -C $BATON/context-db new TYPE=epic DOMAIN=<domain> SLUG=key-123-foo TITLE="Foo epic"
#   make -C $BATON/context-db index          # regenerate INDEX.md (the PostToolUse hook runs it after every change)
#   make -C $BATON/context-db verify         # validate frontmatter + INDEX freshness (CI-style gate)
#   make -C $BATON/context-db find TAG=pii            # docs carrying a tag
#   make -C $BATON/context-db find DOMAIN=<domain>    # docs in a domain
#   make -C $BATON/context-db archive SLUG=opine      # flip a doc to status: archived
#
# Live session registry (who is working on what, right now — see SESSION_INDEX.md):
#   make -C $BATON/context-db session-register NAME=<name> EPIC=KEY-123 REPOS=<repo> \
#                         WORKING="KEY-456 go-live" RESP="<what this session owns>" REF=<ref>
#   make -C $BATON/context-db session-touch NAME=<name>  # <=12h keep-alive heartbeat; WORKING= records a new focus
#   make -C $BATON/context-db session-end NAME=<name>    # mark ended; writes ## Session stats + ## What this session did + sessions/_ledger.md row
#   make -C $BATON/context-db session-rename FROM=<old> TO=<new>  # move+re-frontmatter the entry; a running heartbeat restarts under TO
#   make -C $BATON/context-db session-stats             # print this session's stats block (turns, ctx, tokens, ~$, PRs…)
#   make -C $BATON/context-db session-activity          # what this session did, grouped (files/commits/PRs/tickets/drafts/compactions), from the transcript
#   make -C $BATON/context-db session-index             # regenerate SESSION_INDEX.md (MAX_ENDED=5 rows of ended sessions)
#   make -C $BATON/context-db session-archive           # sweep ended sessions (> ARCHIVE_DAYS=7 old, or no next prompt for NOPROMPT_HOURS=48) to sessions/archive/; DRY=1 lists only
#   Every session-* target refreshes the row's `stats:` line from the transcript named by
#   $CLAUDE_CODE_SESSION_ID (or SESSION_ID=<uuid>); NOSTATS=1 skips it. Zero model turns.
#
# Kit / config (env fact store .context/reference/env/):
#   make -C $BATON/context-db kb ARGS="get slack.channel help"       # kb.py: get|set|rm|list|values|config|config-set|init|path|migrate|discover|stale
#   make -C $BATON/context-db kb ARGS="set slack.channel foo C0… --from tool:slack_search_channels"
#   make -C $BATON/context-db profile                   # active environment name
#   make -C $BATON/context-db profile KEY=tracker.kind  # one env-config value
#   make -C $BATON/context-db kit-verify [STALE=90]     # every skill/agent carries versioning frontmatter; env store complete
#   make -C $BATON/context-db verify-skill [UNIT=skills/x] # env-free validator: frontmatter schema, body, leak shapes — no .context/ needed
#   make -C $BATON/context-db ci                        # every gate .github/workflows/ci.yml runs (ALLOW_SKIP=1: tolerate a missing tool)
#   make -C $BATON/context-db check-links               # every relative Markdown link and `<file> § <heading>` pointer resolves; docs/ says $BATON, not .claude/
#   make -C $BATON/context-db install-smoke             # README's clone + plugin install blocks on a scratch HOME (plugin needs `claude`)
#   make -C $BATON/context-db shellcheck                # shellcheck -S warning over the shell scripts and hooks
#   make -C $BATON/context-db plugin-validate            # plugin + marketplace manifests and every skill/agent, through `claude plugin validate --strict` (needs the `claude` CLI)
#   make -C $BATON/context-db review-gate [BASE=origin/main] [SKIP_BUMP=1]  # tier 0 of the review: leak shapes on added lines, bumps
#   make -C $BATON/context-db review-gate-tree                                # leak shapes over EVERY tracked file (#95), not the diff
#   make -C $BATON/context-db review-evidence [BASE=origin/main]           # the evidence file the reviewer reads (.review/evidence.md)
#   make -C $BATON/context-db test [T=test_kb]          # the engine's stdlib unittest suite (context-db/tests/) on a throw-away store; T= runs one file
#   make -C $BATON/context-db eval-check                # static check of evals/ (no tokens): case format, trigger suites
#   make -C $BATON/context-db eval [SKILL=pr-open] [MODEL=<id>] [RUNS=3]  # run the eval suite (SPENDS TOKENS)
#   make -C $BATON/context-db sync-check                # warn when the kit checkout is ahead of origin or the last sync errored
#   make -C $BATON/context-db kit-health [STALE=90] [KIT_HEALTH_ARGS=…]  # full kit audit of the store CONTEXT names (the kit-health skill's script)
#   make -C $BATON/context-db engine-cli-doc           # regenerate docs/engine-cli.md from this help + every tool's --help (tests fail on drift)
#
# Content root: CONTEXT overrides the default described in docs/layout.md's "Content root" paragraph.
# Point the engine at a different content DB with, e.g.:
#   make -C $BATON/context-db index CONTEXT=/path/to/some/.context
# Always run `index` after adding or editing a doc's frontmatter.

ENGINE  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BIN     := $(ENGINE)bin
# CONTEXT defaults to `kit_profile.py context` — the engine's one content-root resolver (docs/layout.md).
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

.PHONY: help new index verify find archive test eval eval-check session-register session-touch session-end session-rename session-stats session-activity session-index session-archive kit-verify verify-skill ci profile kb sync-check kit-health review-gate review-gate-tree review-evidence plugin-validate check-links shellcheck engine-cli-doc install-smoke
# An explicit `SESSION_ID=` (command line or shell environment) wins as-is; otherwise take the CURRENT, expanded
# value of CLAUDE_CODE_SESSION_ID (`:=`, not `?=`/`=`, which would store the unexpanded reference text — `sq` below
# reads SESSION_ID with $(value …), so a lazily-expanded default would leak the literal "$(CLAUDE_CODE_SESSION_ID)").
ifeq ($(origin SESSION_ID),undefined)
SESSION_ID := $(CLAUDE_CODE_SESSION_ID)
endif
STATSFLAGS  = $(if $(value SESSION_ID),--session-id $(call sq,SESSION_ID),) $(if $(value NOSTATS),--no-stats,)

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

# make kit-health [STALE=90] [CONTEXT=<store>] [KIT_HEALTH_ARGS="--quiet …"]  — the audit reads the store CONTEXT names
# (CONTEXT_ROOT for the script and every child it runs) and writes nothing to it unless KIT_HEALTH_ARGS carries --stamp.
kit-health:
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(ENGINE)../skills/kit-health/kit-health.py --stale $(or $(STALE),90) $(KIT_HEALTH_ARGS)

# make engine-cli-doc  — docs/engine-cli.md, generated from `make help` + every engine tool's --help;
# tests/test_engine_cli_doc.py fails CI when the committed doc drifts from this output.
engine-cli-doc:
	@$(PY) $(BIN)/gen_engine_cli.py

# TITLE= is often a ticket or PR title: each value reaches new.sh unexpanded and single-quoted (`$(call sq,VAR)`),
# and `unexport` keeps make from expanding it into the recipe environment, so `$(…)`, quotes and backticks are data (#139).
override sq = '$(subst ','\'',$(value $(1)))'
unexport TYPE DOMAIN SLUG TITLE
new:
	@dest=$$(CONTEXT_ROOT="$(CONTEXT)" TYPE=$(call sq,TYPE) DOMAIN=$(call sq,DOMAIN) SLUG=$(call sq,SLUG) TITLE=$(call sq,TITLE) sh $(BIN)/new.sh) && \
		echo "created $$dest" && \
		$(MAKE) --no-print-directory -f $(lastword $(MAKEFILE_LIST)) index

index:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) gen_index.py

verify:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) verify.py

# make test [T=test_kb]  — the engine's stdlib unittest suite (context-db/tests/), ALWAYS on a throw-away blank store
# built as CI builds it (`kb.py init --blank`, environment "ci"): never this machine's .context/, whatever CONTEXT,
# CONTEXT_ROOT or the directory it runs from (a kit worktree resolves the workspace's live store). The suite refuses a
# CONTEXT_ROOT outside the temp dir (tests/__init__.py), so a test can never write a real store. T=<module> runs one
# file (tests/test_kb.py) instead of the full discovery. ISOLATED=1, the old opt-in, is accepted and changes nothing.
test:
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; ctx="$$tmp/.context"; \
	  CONTEXT_ROOT="$$ctx" $(PY) $(BIN)/kb.py init --blank >/dev/null && CONTEXT_ROOT="$$ctx" $(PY) $(BIN)/kb.py config-set environment ci >/dev/null && \
	  cd $(BIN)/.. && CONTEXT_ROOT="$$ctx" env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_JOB_DIR -u KIT_SCRATCH $(PY) -m unittest $(if $(T),tests.$(T),discover -s tests -t .) -v

# make kit-verify [STALE=90]  — every skill/agent carries the versioning frontmatter
kit-verify:
	@cd $(BIN) && $(PY) kit_verify.py $(if $(STALE),--stale $(STALE),)

# make verify-skill [UNIT=skills/<name>|agents/<name>.md …]  — the environment-free validator: the same
# frontmatter, body and leak-shape checks as kit-verify, no env store or .context/ needed, so a contributor
# runs it on a bare clone. UNIT (not PATH: a make variable named PATH would shadow the shell's) is relative
# to the kit root; without it every unit is checked.
verify-skill:
	@cd $(ENGINE).. && $(PY) context-db/bin/kit_verify.py --no-env $(UNIT)

# make ci [BASE=origin/main] [ALLOW_SKIP=1]  — every gate .github/workflows/ci.yml runs, so a green `make ci` predicts a
# green CI: the review gate on this branch's diff and over the tree, the env-free validator, kit-verify + kit-health --ci
# against a throw-away blank store, the engine tests, the eval static gate, the plugin manifests, links, shellcheck,
# py_compile, bash -n and the dash parse, and the README install smoke. A gate whose tool is missing here (shellcheck,
# `claude`) FAILS unless ALLOW_SKIP=1 — a skipped gate must never read as a pass. Not here: the token-spending evals.
ci: review-gate review-gate-tree verify-skill kit-verify-blank test eval-check plugin-validate check-links shellcheck parse install-smoke
	@echo "make ci: OK — the CI gates ran$(if $(ALLOW_SKIP), (ALLOW_SKIP=1: see the \"skipped\" lines above),)"

# kit-verify and kit-health's CI scan against a throw-away blank store, as CI builds it (environment "ci")
kit-verify-blank:
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; export CONTEXT_ROOT="$$tmp/.context"; \
	  $(PY) $(BIN)/kb.py init --blank >/dev/null && $(PY) $(BIN)/kb.py config-set environment ci >/dev/null && \
	  (cd $(BIN) && $(PY) kit_verify.py) && $(PY) $(ENGINE)../skills/kit-health/kit-health.py --ci --quiet

# every Python file compiles; every shell script parses with bash; every `#!/bin/sh` script (they run under `sh`,
# dash on most hosts) parses with dash too — ci.yml calls this target, so the selection lives in one place
parse:
	@cd $(ENGINE).. && git ls-files -z '*.py' | xargs -0 -r $(PY) -m py_compile && echo "py_compile: OK"
	@cd $(ENGINE).. && git ls-files -z '*.sh' hooks/ ':!hooks/*.json' | xargs -0 -n1 bash -n && echo "bash -n: OK"
	@cd $(ENGINE).. && rc=0; sh_files="$$(git ls-files '*.sh' hooks/ ':!hooks/*.json' | while read -r f; do head -1 "$$f" | grep -q '^#!/bin/sh' && echo "$$f"; done)"; \
	  if command -v dash >/dev/null 2>&1; then p=dash; else p=sh; echo "parse: no dash here — sh -n instead (CI uses dash)"; fi; \
	  for f in $$sh_files; do $$p -n "$$f" || { echo "$$p -n failed: $$f"; rc=1; }; done; \
	  [ $$rc -eq 0 ] && echo "sh -n ($$p): OK — $$(echo $$sh_files | wc -w) #!/bin/sh script(s)"; exit $$rc

# make eval-check [REQUIRE_ALL=1]  — the eval suite's static gate (#54; no model, no tokens): every case loads
# (prompt.md keys, graders of a known type), every trigger suite has >= 10 cases with positives and near misses.
# Skills without a suite and descriptions with no recognized trigger phrase (TRIGGER_WHEN / TRIGGER_OTHER) are
# notes on a bare run; ci.yml runs REQUIRE_ALL=1, so both are a failure there.
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
	@cd $(ENGINE).. && $(PY) context-db/tests/install_smoke.py clone && \
	  if command -v claude >/dev/null 2>&1 || [ -z "$(ALLOW_SKIP)" ]; then $(PY) context-db/tests/install_smoke.py plugin $(if $(ALLOW_SKIP),,--require-cli); \
	  else echo "install-smoke plugin: skipped (no \`claude\` CLI; ALLOW_SKIP=1)"; fi

# shellcheck -S warning over every tracked shell script and the git hooks: the system binary, else the
# PyPI wheel through uvx; a machine with neither says so (CI has uv).
shellcheck:
	@cd $(ENGINE).. && files="$$(git ls-files '*.sh' | grep -v /fixtures/) hooks/pre-push hooks/commit-msg"; \
	  if command -v shellcheck >/dev/null 2>&1; then shellcheck -S warning $$files && echo "shellcheck: OK"; \
	  elif command -v uvx >/dev/null 2>&1; then uvx --from shellcheck-py shellcheck -S warning $$files && echo "shellcheck: OK (uvx)"; \
	  elif [ -n "$(ALLOW_SKIP)" ]; then echo "shellcheck: skipped (neither shellcheck nor uvx on this machine; ALLOW_SKIP=1)"; \
	  else echo "shellcheck: FAILED — neither shellcheck nor uvx on this machine (install one, or ALLOW_SKIP=1 to skip it knowingly)"; exit 1; fi

# make plugin-validate  — the plugin and marketplace manifests + every skill/agent, through Claude Code's own validator
# (`claude plugin validate --strict`, docs/packaging.md). A clone without the `claude` CLI says so instead of failing:
# kit-verify still checks the version discipline (plugin.json version == VERSION) on every machine.
plugin-validate:
	@cd $(ENGINE).. && if command -v claude >/dev/null 2>&1; then claude plugin validate --strict . && claude plugin validate --strict skills >/dev/null && claude plugin validate --strict agents >/dev/null && echo "plugin validate: OK"; \
	  elif [ -n "$(ALLOW_SKIP)" ]; then echo "plugin validate: skipped (no \`claude\` CLI on this machine; ALLOW_SKIP=1)"; \
	  else echo "plugin validate: FAILED — no \`claude\` CLI on this machine (npm i -g @anthropic-ai/claude-code, or ALLOW_SKIP=1)"; exit 1; fi

# Tier 0 and tier 1 of the review (docs/REVIEW.md): the gate fails on a leak shape in an added line or a changed
# unit without a version/updated bump (SKIP_BUMP=1 for a wording-only change); the evidence file is what the CI
# reviewer reads (.review/ is git-ignored).
BASE ?= origin/main
review-gate:
	@cd $(ENGINE).. && git rev-parse -q --verify "$(BASE)^{commit}" >/dev/null || { echo "review-gate: base $(BASE) not found — git fetch origin main (or BASE=<ref>)"; exit 1; }; \
	  $(PY) $(BIN)/review_gate.py --base $(BASE) --head HEAD $(if $(SKIP_BUMP),--skip-bump,)
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
# Free text (WORKING="…", RESP="…") reaches session.py verbatim (#132): `$(call sq,VAR)` is the variable's
# UNEXPANDED value as one single-quoted sh word, and `unexport` keeps make from expanding it into the recipe
# environment (a `$(shell …)` inside the text would otherwise run). So quotes, `$(…)` and backticks are data.
SESSION_VARS := NAME REF STATUS EPIC REPOS WORKING WORKING_IF_EMPTY RESP NOTE NEXT SESSION_ID NOSTATS FROM TO
unexport $(SESSION_VARS)
override sq = '$(subst ','\'',$(value $(1)))'
SESSION_ARGS = --ref $(call sq,REF) --status $(call sq,STATUS) --epic $(call sq,EPIC) --repos $(call sq,REPOS) \
               --resp $(call sq,RESP) --note $(call sq,NOTE)
session-register:
	@test -n $(call sq,NAME) || { echo "usage: make session-register NAME=<session> [EPIC=] [REPOS=] [WORKING=] [RESP=] [REF=] [STATUS=]"; exit 2; }
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py register --name $(call sq,NAME) $(SESSION_ARGS) --working $(call sq,WORKING) $(STATSFLAGS)
	@sh $(ENGINE)../sync-check.sh || true

session-touch:
	@test -n $(call sq,NAME) || { echo "usage: make session-touch NAME=<session> [WORKING=] [STATUS=]"; exit 2; }
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py touch --name $(call sq,NAME) --status $(call sq,STATUS) --working $(call sq,WORKING) --working-if-empty $(call sq,WORKING_IF_EMPTY) --next $(call sq,NEXT) $(STATSFLAGS)

session-end:
	@test -n $(call sq,NAME) || { echo "usage: make session-end NAME=<session> [NEXT=<file with the next-session prompt>|none] [WORKING=]"; exit 2; }
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py end --name $(call sq,NAME) --working $(call sq,WORKING) --next $(call sq,NEXT) $(STATSFLAGS)

session-rename:
	@test -n $(call sq,FROM) || { echo "usage: make session-rename FROM=<old-name> TO=<new-name>"; exit 2; }
	@test -n $(call sq,TO) || { echo "usage: make session-rename FROM=<old-name> TO=<new-name>"; exit 2; }
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py rename --from $(call sq,FROM) --to $(call sq,TO)

# make session-stats [SESSION_ID=<uuid>] — the stats block for the handoff (no registry write)
session-stats:
	@CONTEXT_ROOT="$(CONTEXT)" $(PY) $(BIN)/session.py stats $(STATSFLAGS)

# make session-activity [SESSION_ID=<uuid>] — what this session did (files/commits/PRs/tickets/drafts/
# compactions), derived from the transcript at flush time; no registry write. Same SESSION_ID override
# as session-stats.
session-activity:
	@$(PY) $(BIN)/session_stats.py --activity $(if $(value SESSION_ID),--session-id $(call sq,SESSION_ID),)

session-index:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) gen_sessions.py $(if $(NOARCHIVE),--no-archive,)

# make session-archive [ARCHIVE_DAYS=<n>] [NOPROMPT_HOURS=<h>] [DRY=1] — same sweep session-index runs, on its own;
# DRY=1 only lists. NOARCHIVE=1 (here or on session-index) regenerates without sweeping.
session-archive:
	@cd $(BIN) && CONTEXT_ROOT="$(CONTEXT)" $(PY) gen_sessions.py $(if $(DRY),--dry-run,) $(if $(NOARCHIVE),--no-archive,)

# make archive SLUG=<file-slug>  — flip status to archived in place (kept in INDEX).
# Atomic: sed writes a temp copy in the same directory, `mv` replaces the doc in one rename — a reader
# (or a second `make archive`/`make index` racing this one) sees the old doc or the new one, never a
# half-written one; the temp copy is removed if the sed or the mv fails, so nothing is left behind.
# `cp -p` (portable — BSD/macOS has no `chmod --reference`) seeds the temp copy with the doc's own mode
# before sed overwrites its content, so the mode survives the swap instead of settling at mktemp's 0600.
archive:
	@test -n "$(SLUG)" || { echo "usage: make archive SLUG=<file-slug>"; exit 2; }
	@f=$$(find $(CONTEXT) -name '$(SLUG).md' -not -path '*/_templates/*' | head -1); \
	 test -n "$$f" || { echo "no doc named $(SLUG).md"; exit 1; }; \
	 tmp=$$(mktemp "$$f.XXXXXX"); \
	 if cp -p "$$f" "$$tmp" && sed -E 's|^status:.*|status: archived|' "$$f" > "$$tmp" && mv -f "$$tmp" "$$f"; then \
	   echo "archived $$f (still indexed; move to archive/ by hand if you want it out of the domain view)"; \
	 else rm -f "$$tmp"; exit 1; fi; \
	 $(MAKE) --no-print-directory -f $(lastword $(MAKEFILE_LIST)) index
