# Environment facts — the store, the CLI, and how a skill resolves one

**Rule.** A skill, agent, engine script or shared doc in the kit never carries a value that differs
between the environments the kit runs in — no Slack channel id, Jira custom-field id, transition id,
AWS account id, Notion database id, colleague login, org host, ticket key. It names the *fact* it
needs and resolves it at run time from the **env fact store**. The kit ships mechanisms; the facts
are a knowledge base that each machine builds as the kit is used.

## The store — `.context/reference/env/`

Part of the `.context/` document DB (local, never synced), created by `setup.sh` (`kb.py init`).

```
.context/reference/env/
├── config.json     # structural switches scripts branch on (see below) + "environment" + "domains"
├── slack.md        # ## channel · ## review-venue · ## user
├── tracker.md      # ## setting · ## field · ## transition · ## user · ## query
├── github.md       # ## person · ## team · ## label-set
├── aws.md          # ## account · ## profile · ## region · ## cluster
├── notion.md       # ## database · ## page
├── datalake.md     # ## setting · ## tool
├── _templates/     # optional context-doc scaffolds (epic.md, oncall.md, …) that override the engine's
└── _discovery/     # optional discovery manifests a plugin shipped (`kb.py import <dir>` copies them here)
```

The environment's **prose** (domains, capabilities it turns on, conventions, rules, repo map) is the sibling doc
`.context/reference/environment.md`, imported by the root `CLAUDE.md` next to `.claude/WORKSPACE.md`;
skeleton in `.claude/environment-template/environment.md`.

Each `<system>.md` is a normal `.context/` reference doc (frontmatter `type: reference`, `tags: [env,
<system>]`, `status: reference`) whose body is one table per **kind**:

```
## channel

| name | value | purpose | learned-from |
|---|---|---|---|
| eng-help | C0… | the team's help channel; review venue for <repo> | tool:slack_search_channels 2026-09-24 |
```

- `name` — stable handle skills use (`eng-help`, `sprint`, `cancelled`, `prod`); lowercase, no spaces.
- `value` — the literal id/login/host.
- `purpose` — why a skill would want it (one clause).
- `learned-from` — **structured provenance** `<who> <YYYY-MM-DD>`, who = `tool:<tool-name>` (a discovery tool
  answered), `user` (the user said so — never reported stale), `derived:<key>` (computed from another fact).
  `kb.py set --from` accepts exactly these and
  appends today's date; free text is refused, because the date is what `kb.py stale` measures. Rows written
  before this convention (free text, 2026-09-25 and earlier) still read fine — `stale` takes the last date
  in the cell and reports an undated row as stale.

**Kinds are canonical.** A heading a session invents for an existing kind (`## channels` for `channel`) is
a *renamed kind*: `kb.py` lists it in `RENAMED_KINDS`, `get`/`set` and the `kit_profile.py` projection
answer through the canonical name at once, `kb.py migrate` moves the rows (a name present under both with
different values is reported as a `CONFLICT` and left for the user), and `kit-verify` fails a store that
still carries one. Add a new kind only when no existing one means the same thing — `kb.py discover --all`
lists the vocabulary.

`config.json` carries only what scripts branch on: `tracker.kind` (`jira`|`github`), `tracker.key_regex`,
`tracker.url_template`, `tracker.mcp_tools`, `tracker.close_reasons`, `tracker.repos`, `github.org`,
`github.review_bot`, `github.bots`, `github.signed_commits`, `github.sandbox_token_prefix`,
`github.owner_teams`, `slack.enabled`, `slack.domain`, `systems.*`, `tz_default`, `labels`,
`self_assessment`, `diagrams` (pr-open's per-repo diagram overlays), `commits` (commit-subject style, `commit-style.md`), `datalake`, `cost` (cost-report's spend table,
identity, phases — optional, absent = private mode), `datalake.kind` (warehouse vendor — optional, picks the
`datalake-<vendor>` manifest), `kit.install_mode` (`clone`|`plugin`|`dev-checkout` — written by `setup.sh`, never by
hand; kit-health § 1 compares it with how the kit actually runs, #34), `leaks.markers` (optional: literal strings — a sandbox product's CLI or env-file path, a tenant or team name — that kit-health's leak scan adds to the configured values; for what no generic shape can know, #95), `kit.sandbox_markers` (optional: absolute
paths or environment-variable names whose presence means this machine runs in a sandbox, so kit-health warns when
`github.sandbox_token_prefix` is empty there; absent = no sandbox check, #94), plus `environment` (this
environment's name, a lowercase slug) and `domains` (extra `.context/` folders). Template with every
key: `.claude/environment-template/config.json`; `kit-verify` checks the store against it.
Everything that is an id or a name is a table row, not a config key.

## The CLI — `context-db/bin/kb.py` (stdlib, also `make -C .claude/context-db kb ARGS="…"`)

```
kb.py get <system>.<kind> <name>                      # prints the value; exit 1 + hint when missing; a stale-row
                                                      #   hint on stderr when it is older than its ttl (value still printed)
kb.py set <system>.<kind> <name> <value> [--purpose …] [--from tool:<name>|user|import:<env>|derived:<key>]
                                                      # upsert (bumps the doc's updated:); default --from user. Same value +
                                                      #   an explicit --from = `re-verified`: the provenance is re-dated, so a
                                                      #   stale row clears; an empty name is exit 2
kb.py rm  <system>.<kind> <name>
kb.py list [<system>[.<kind>]]                        # tab-separated rows
kb.py values                                          # every literal value (kit-health's leak scanner)
kb.py discover <system>.<kind> [<name>] | <config.key> # the discovery PLAN for one fact (below) — never calls the tool
kb.py discover --all | --check                        # every manifest fact with its state here | validate the manifests
kb.py stale [--days N] [--check]                      # tool/import/derived rows older than their ttl_days (--check: exit 3)
kb.py config [<dotted.key>] · kb.py config-set <dotted.key> <json-or-string>
kb.py migrate [--check|--off]                         # schema catch-up: renamed flags/kinds, missing flags (--check: exit 3 when pending; --off: just the not-applicable units)
kb.py init --blank                                    # create an empty store (setup.sh does this)
kb.py init --personal                                 # blank store + the zero-config GitHub-only fill: identity from gh,
                                                      # repos from the workspace clones, tz from the OS (setup.sh --personal)
kb.py path
```

`kit_profile.py get <dotted.key>` is the read API every script and skill uses: it loads the store's
`config.json` and projects the tables onto the dotted keys skills were written against (`slack.channels`,
`slack.repo_channels`, `tracker.<setting>`, `tracker.sprint_field`/`unplanned_field`,
`tracker.transitions`, `github.display_names`). `kit_profile.py name` prints the environment name,
`domains` the extra domains, `template <type>` the store's override path (empty when none), `source`
`env` or `none`, `scratch [--stable] [sub]` a scratch directory that exists on every machine (per session, or per user
with `--stable`; the job directory where a sandbox sets `CLAUDE_JOB_DIR`) — the one path skills and scripts use for
scratch files, never a bare `/tmp`.

## Discovery manifests — `context-db/discovery/<system>.json`

Every fact the kit knows how to find has a manifest entry: the tool that answers it (an MCP tool name, or
`cli` / `roster` / `settings` / `derive` / `user`), an args template (`{name}`, `{config.<key>}`,
`{row.<system>.<kind>.<name>}`), a one-clause **verify**, a **ttl_days** after which a tool-sourced row is
reported stale (`0` = never), and the write **target** (`row` in `<system>.md`, or a `config.json` key —
most fact-reading units read config keys, so the manifest owns those too). A manifest may carry
`requires: <flag>` (a unit says the same as `metadata.requires: "<flag>"`) or `when: {config, equals}` so `discover` says *not applicable here* instead of
sending a session to a tool the machine does not have. The *pick* heuristics (which candidate is "the"
one) stay prose in the skill that needs the fact. Schema and field table: `context-db/discovery/README.md`.
Manifests: `slack`, `tracker-jira`, `tracker-github`, `github`, `aws`, `notion`, `datalake-snowflake`,
`airflow`, `dbt`, `incident-io`, `lattice`, `workspace` — one per capability flag plus the workspace itself (`ls context-db/discovery/` is the list; this sentence is not). A manifest may carry `"facts": []` with a `note`: the flag exists, no unit declares a store fact for it yet — `lattice`'s only input is an identity variable, never a store row. Two manifests may own the same key only when their `when` guards exclude each other
(`tracker-jira` / `tracker-github` on `tracker.kind`; a vendor's `datalake-<vendor>` on `datalake.kind`) — `discover`
follows the applicable one, `discover --check` rejects an unguarded duplicate. The switch a `when` reads
(`tracker.kind`, `datalake.kind`) is itself a `user` fact in `workspace.json`, so the choice is never circular. A plugin ships its own as `<dir>/discovery/*.json` (`kb.py import <dir>` copies them into the
store's `_discovery/`, same file name wins). `kit-verify` validates the kit's manifests and every unit's
`metadata.facts` frontmatter against them.

## How a skill resolves a fact

1. **Look it up:** `python3 .claude/context-db/bin/kb.py get slack.channel eng-help`. Found → done (a
   stderr hint means the row is past its ttl: re-verify when the task can afford it, never block on it).
2. **Plan the discovery** when missing: `kb.py discover slack.channel eng-help` prints the tool call, the
   verify step and the exact write-back command (or *NOT APPLICABLE here*, or *no manifest* → step 3).
3. **Run the tool and verify** as the plan says. Prefer a tool result over a guess; never infer an id
   from a similar-looking one. **Ask the user once** only when the tool cannot settle it (which of three
   channels is "the" review venue; which account is prod) — one question, with the candidates.
4. **Write it back:** `kb.py set slack.channel eng-help C0… --purpose "…" --from tool:slack_search_channels`
   (or `--from user`) — so no later session asks again. Never store a secret (token, session name, SSM
   value) here; the store is a plain file in `.context/`.

A skill declares the facts it needs as `facts` under its `metadata:` frontmatter, one comma-separated string —
`facts: "slack.channel,tracker.field sprint,cost.spend_table"` (`<system>.<kind>` for any row of a kind,
`<system>.<kind> <name>` for one handle — a space separates kind and name — a dotted key for a config fact;
`context-db/bin/frontmatter.py` is the one parser, `frontmatter.py facts` lists every declared entry);
`kit-verify` rejects an entry no manifest covers. A forked
worker that hits a missing fact returns exactly `NEEDS <system>.<kind> <name>` and the main session
resolves it as above. Refresh: `kb.py stale` (monthly in `kit-health`) lists the rows past their ttl with
their `discover` command; a `user` row is never stale. The bulk paths — a new machine, `--refresh` of the
stale rows, or the one fact a skill stopped on — are the `env-init` skill (`skills/env-init/SKILL.md`),
which executes the plans and holds the "one batched user round" rule.
`kit-health` § leaks flags any literal that should have been a lookup: generic shapes (Slack ids of
every real length, `customfield_n`, 12-digit account ids, ticket keys, memory-note pointers, 32-hex
ids, org hosts, tz literals, wording that assumes one sandbox, the mount path and job-directory variable only a
sandbox has) plus every value this environment's store and config actually hold.

## Team layer

Facts every member of a team shares (the review venues, the sprint field, the on-call channel) are
still per-machine rows — a team can ship them as **seed tables** in its own plugin and write them with
`kb.py set` in a setup step. The kit itself never carries them, and it has no second repo for them
(`new-environment.md` § History).
