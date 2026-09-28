---
name: kit-health
description: Audit the kit on this machine: versioning frontmatter, env-value leaks in kit files, env-store coverage and stale rows, wiring (CLAUDE.md imports, memory symlink, CLIs, systems), an engine smoke, the green stamp. Walks each finding (Fix, Ticket, Accept), reports to `.context/kit-health/`, stamps `HEALTH-<env>.md`. Run after `make claude_sync`, monthly, and when a skill misbehaves.
metadata:
  version: "48"
  updated: "2026-09-27"
  reviewed: "2026-09-26"
  facts: "aws.profile kit-health"
user-invocable: true
---

# kit-health — one run per environment, green everywhere

A run proves the kit is sound (frontmatter, no environment value in a kit file, the engine loads) and that
**this** machine is wired to its env store; each machine stamps `.context/kit-health/HEALTH-<env>.md`.

1. **Run.** `SCRATCH=$(python3 $BATON/context-db/bin/kit_profile.py scratch); echo "report: $SCRATCH/kit-health.md"`
   then `python3 $BATON/skills/kit-health/kit-health.py --stale 90 --quiet --report "$SCRATCH/kit-health.md"`
   and Read the echoed path (shell variables do not survive between tool calls). Exit 0 GREEN · 1 AMBER ·
   2 RED. Read-only: only `--stamp` writes. Sections 1–6 (kit, leaks, config, machine, engine, stamp); § 1 names
   the install mode (warns when it differs from the one setup.sh recorded) and
   warns when a newer kit release is out and names the update command; § 6 lists the units changed since the
   last stamp. A stale row in § 3: `/env-init --refresh`, never a hand-dated row.
2. **Judgement pass** — only when § 6 lists changed units or a unit is flagged: fork `triage` with
   *"For each of these SKILL.md/agent files answer in one line, `<unit> · OK | <finding>`: is the
   `description` still what the body does?"* Everything else is machine-checked.
3. **MCP systems**: one cheap read call per enabled MCP-backed `systems.*`; a missing tool is one WARN
   `<system>: enabled but not connected here`. Never a write.
4. **Walk the findings** — one `AskUserQuestion` per finding (batch trivial ones): **Fix** (do it, re-run), **Ticket** (`ticket-open`, title `[KIT] …`, unassigned), **Accept** (leaks only: add the
   anchored `path:value` regex to `skills/kit-health/allow.txt` with a `# reason`; a stale unit is accepted
   by bumping `metadata.reviewed` after re-reading it). A leak's Fix: `kb.py set …`, then the skill
   reads it back. A Fix that touches a skill bumps `metadata.version` + `metadata.updated`. **Every edit to a kit
   file** (an Accept, a Fix) is made in a worktree of a kit **checkout** off `origin/main`, never under `$BATON`
   on a plugin install: that is Claude Code's plugin cache, and `claude plugin update` overwrites it (§ 1 warns
   about a file changed there). No checkout yet: `gh repo clone <kit repo>` (§ 1 names it) into the workspace.
5. **Report.** One log per day and environment: `make -C $BATON/context-db new TYPE=log DOMAIN=kit-health
   SLUG=<YYYY-MM-DD>-<env> TITLE="kit-health <date> · <env>"` (the date of the report header), paste the report
   and each finding's outcome, `make … index`. When that log already exists (an earlier run today, or the re-run
   after a Fix), `new` refuses to clobber it: add a `## Run <HH:MM>` section with the same content at the top of
   its body instead (newest first, as in every log).
6. **Stamp.** Re-run step 1 with `--stamp`: it refuses on any error **or un-accepted leak hit**, else
   writes the HEALTH doc (`last_green`, `kit_commit`, `kit_version`, `install_mode`, `warnings`) and re-indexes.
7. **PR.** Kit files the walk touched go out as one PR from that worktree (`pr-open`); after the merge (and
   `claude plugin update` on a plugin install), a run on each machine stamps it. Tell the user the verdict.

The script carries no environment's values: generic shapes plus this machine's configured values
(redacted). Optional fact `aws.profile kit-health`: the `aws_sso` probe's profile.
