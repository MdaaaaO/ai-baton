# Conventions that keep the kit portable

Six rules that let one kit run on any machine without a fork. The values they keep out of the kit live in the
env store (`docs/env-facts.md`); the file map is `docs/layout.md`.

- **Kit paths are `$BATON/…`, workspace paths are relative to the workspace root.** The kit lives in
  `.claude/` on a clone and in Claude Code's plugin cache on a plugin install, so docs and unit bodies say
  `$BATON/skills/pr-watch/pr-watch.sh`, never `.claude/…` and never an absolute path (`docs/packaging.md`
  § Kit root). Sessions run in the workspace root, so its own files are plain relative paths
  (`.context/reference/env/`). Scripts find the kit from their own location, never through `$BATON`, and
  `.context/` through `kit_profile` (`context_root()`, `kit_profile.py context`), never from their own location
  (on a plugin install that is the plugin cache); env
  overrides exist where a script had one before (`PR_REVIEW_HOME`, `SIGN_QUEUE_DIR`, `SIGN_QUEUE_ROOT`).
- **Identity is `WORKSPACE_*`, from two sources, one reader.** On a plugin install Claude Code collects the five
  values through `userConfig` (`/plugin configure ai-baton`; the chat ids are `sensitive`, stored in
  secure storage, never in a JSON file) and a SessionStart hook re-exports them as `WORKSPACE_*` for Bash; on a
  clone they are the `env` of the ignored `settings.local.json`, merged into every Bash, hook and subagent
  environment. `kit_profile.identity()` reads the option first, then the variable, so a value typed into
  `/config` wins over a stale file. Tokens go in neither (docs/contributing.md § Secrets). `pr-watch.sh` drops your own events via `WORKSPACE_GITHUB_LOGIN`,
  `enqueue.sh` stamps `--by "$WORKSPACE_USER"`, the session registry renders times in
  `WORKSPACE_TZ`. Skill prose says "the user", never a name.
- **Environment facts come from the env store, never from a skill.** Anything that differs between
  the places the kit runs — tracker kind and project, GitHub org and review bot, Slack channel ids,
  custom-field and transition ids, AWS account ids, which systems exist (`systems.*`), the timezone
  default — lives in `.context/reference/env/` (`kb.py get`, `kit_profile.py get <key>`) or, as prose,
  in `.context/reference/environment.md`. A `SKILL.md`, agent, engine script or `WORKSPACE.md`
  never carries such a value: it names the fact it needs (`metadata.facts` frontmatter) and resolves it (`kb.py get`
  → `kb.py discover` plan from the discovery manifests → tool + verify → ask the user once → `kb.py set`;
  in bulk: the `env-init` skill). A skill that needs a system the configuration lacks says "not
  applicable in this environment" and stops. `kit-health` scans every kit file for the generic
  shapes of such values and for every value this environment has configured.
- **Personal state lives in the workspace's `.context/`**, never in the kit: auto-memory in `.context/memory/`,
  review ledger and submitted-review digests in `.context/state/pr-review/`.
- **Shared instructions live in `WORKSPACE.md`, personal ones in the root `CLAUDE.md`.** The shared
  body reaches every session by import or hook: on a clone, Claude Code resolves `@path` imports
  relative to the importing file, so the one line `@.claude/WORKSPACE.md` in your root `CLAUDE.md`
  pulls it in; on a plugin install the SessionStart hook injects it (`kit_profile.py workspace-rules`)
  and the root `CLAUDE.md` carries no such line.
  Put a rule in `WORKSPACE.md` when every engineer on the team should follow it; put it in
  your preamble when it is about you (how to address you, your own tooling, your own habits).
  (`WORKSPACE.md` is deliberately not named `.claude/CLAUDE.md`: that path is itself a
  memory location Claude Code loads, and the file would be loaded twice. The kit's own `CLAUDE.md` there is
  ≤ 600 B of contributor pointers, loaded into every workspace session of a clone — hence the cap.)
- **Adopting the kit elsewhere** = filling an env store, not forking the skills: `setup.sh` creates a
  blank `.context/reference/env/`, `kb.py config-set` sets the switches, facts arrive as skills ask for
  them, `kit-health` proves the wiring (`docs/new-environment.md`). A shared prose layer for a team
  is a team plugin. `pr-review/config.example.json` is the one per-user file still edited by hand.
