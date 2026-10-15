# Config — the `cost` keys and the tracker query

Read on demand from `SKILL.md`. Shape of the whole key: `environment-template/config.json`.

## Config keys

| key | meaning | default |
|---|---|---|
| `cost.spend_table` | warehouse table at grain user × model × day (billed $) | unset → private mode |
| `cost.email` | the user's identity in that table | **required** in org mode — `sql` exits 2 without it |
| `cost.filters` | SQL predicate selecting the Claude Code rows of that table (`""` = none) | **required** in org mode — the predicate is this warehouse's, not the kit's |
| `cost.columns` | the table's names for `date email model cost input output cache_read cache_write` | **required** in org mode (all eight) — proposed from `DESCRIBE TABLE` (`reference/traps.md` § Column mapping); the kit has no default schema |
| `cost.extra_repos` | `owner/repo` outside `github.org` whose PRs count (e.g. the kit repo) | `[]` |
| `cost.phases` | `[{name,start,end}]` — named windows for before/after cuts | none → rolling 7 vs prior 7 only |
| `cost.prices` | `{priced_at, default, models}` — the private-mode / fixed-price $/Mtok table with its date (`SESSION_STATS_PRICES` still wins for the default row) | unset → the built-in table in `session_stats.py` |

Private mode prices every transcript under `~/.claude/projects/` — `<project>/*.jsonl` plus each session's
`subagents/*.jsonl` (`context-db/bin/transcripts.py`; assistant turns, deduped per request id) — at the
`context-db/bin/session_stats.py` price table.

The org-mode warehouse tool is the env fact `datalake.tool sql`, a roster fact: the vendor manifest names the
tool, the session confirms it is callable. `work-prs` takes its token prefix from `kit_profile.py gh-env`.

## Tracker query

(Step 3, a tracker other than `github`.) The query text is an environment fact, not kit content: `kb.py get
tracker.query resolved_by_me` — if missing, `/env-init tracker.query resolved_by_me`: the tracker's manifest
(`tracker-jira` / `tracker-github`, picked by `tracker.kind`) derives the "issues I was assigned to, resolved on or
after `{since}`, oldest first" query in the tracker's own language, verifies it against a real date and stores it
with `{since}` left as the placeholder this skill fills per run (`--from derived:tracker.kind`). A field-selection
trap you hit (some trackers drop the parent field when many fields are requested; paginate the way the tool
paginates) goes into the row's `--purpose`, never into the query text.

Run it through the tracker's search tool (`tracker.mcp_tools.search` — on first use record it with `kb.py
config-set tracker.mcp_tools.search <MCP tool name suffix>`) and save `tickets.tsv` with the header `key created
resolved type parent` (tab-separated, dates `YYYY-MM-DD`) — exactly the issue key, created date, resolved date,
issue type and parent key. Epics are excluded from the count by the script (they are containers, not units);
`parent` feeds the "epics touched" line. Tickets and PRs are the only work units — lines changed are never a
denominator.
