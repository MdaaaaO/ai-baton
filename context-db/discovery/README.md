# Discovery manifests — how each env fact is found

One JSON file per system. A manifest tells a session **which tool answers which fact**, how to verify the
candidate, how long the answer stays trustworthy, and where the value is written (a table row or a
`config.json` key). It never carries a value — values live in the machine's env store
(`.context/reference/env/`, `kb.py`). The manifest is what `kb.py discover` prints a plan from, what
`kb.py stale` measures rows against, and what `kit-verify` checks a unit's `metadata.facts` frontmatter
against. The *pick* heuristics (which of three channels is "the" review venue) stay prose in the
skill that needs the fact; the manifest holds only the mechanics.

```json
{
  "system": "slack",
  "requires": "slack",
  "facts": [
    {"key": "slack.channel", "target": "row", "tool": "slack_search_channels",
     "args": {"query": "{name}"},
     "verify": "slack_read_channel on the id returns messages and the channel name equals {name}",
     "ttl_days": 365, "purpose": "channel id by handle"}
  ]
}
```

| field | meaning |
|---|---|
| `system` | the store doc the rows go to (`slack` → `slack.md`); for `target: config` facts it only groups |
| `requires` | capability flag (`kb.SYSTEMS`) that must be `true` in `config.systems` — off = not applicable here. Optional |
| `when` | `{"config": "<dotted.key>", "equals": <value>}` — applicability by a config value (`tracker.kind`, `datalake.kind`). Optional. The key it reads must be a `target: config`, `tool: user` fact in a manifest without a `when` (`workspace.json`) — `--check` enforces it |
| `facts[].key` | `<system>.<kind>` for a row fact, or the dotted `config.json` key for a config fact |
| `facts[].target` | `row` (default) or `config` |
| `facts[].tool` | an MCP tool name as this session's roster shows it (`slack_search_channels`; a warehouse connector's `sql_exec_tool` is one vendor's name, so vendor SQL lives in a `when`-guarded `<system>-<vendor>.json`), or one of the built-ins: `cli` (a shell command in `args.cmd`), `roster` (is `args.tool` in this session's tool list), `settings` (`args.key` from `.claude/settings.local.json`), `derive` (computed from another fact, `args.from`), `user` (ask-only — no tool can settle it) |
| `facts[].args` | the call template; `{name}` = the row handle, `{config.<key>}` / `{row.<system>.<kind>.<name>}` = another fact |
| `facts[].verify` | how to confirm the candidate before writing it (one clause) |
| `facts[].ttl_days` | after this many days a `tool:`/`derived:`/`import:` row is reported by `kb.py stale`; `0` = never |
| `facts[].purpose` | default `purpose` column for the row |
| `facts[].common_word` | `true` when the values are ordinary words (a first name, a team, a label): kit-health's value scan skips the kind instead of flagging every occurrence of the word |
| `facts[].note` | anything the caller must know (rate limits, membership needed…) |

Provenance written back is structured — `--from tool:<name>`, `user`, `import:<env>`, `derived:<key>` —
and `kb.py set` appends today's date, so `stale` can measure age.

A plugin ships extra manifests as `<dir>/discovery/*.json`; `kb.py import <dir>` copies them to the
store's `_discovery/` (never overwriting) and `kb.py` reads both places; a store file with the same
file name as a kit manifest (`slack.json`) replaces it **whole** — every fact the kit's file had and the
plugin's does not is gone on that machine — so a plugin that only *adds* facts uses its own file name
(`slack-<plugin>.json`); `--check` names each kit manifest a store file shadows. Validate every manifest with `kb.py discover --check` (also run by `kit-verify`).

**One key, one owner.** The same `facts[].key` in two manifests is a silent override, so `--check` rejects it —
unless the two are **mutually exclusive**: the same `when.config` with different `equals` (`tracker-jira.json`
vs `tracker-github.json` on `tracker.kind`, `datalake-snowflake.json` vs a future `datalake-bigquery.json` on
`datalake.kind`). `kb.py discover` and `stale` then follow the manifest that applies on this machine.
