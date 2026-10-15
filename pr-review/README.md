# pr-review / pr-scan tooling

Scripts live in `$BATON/skills/pr-review/scripts/` and `$BATON/skills/pr-scan/`. Their
per-user **state** lives outside this repo, in the workspace's `.context/state/pr-review/` (found through
`kit_profile.py context` on a clone and a plugin install alike; override with `PR_REVIEW_HOME`; `setup.sh` creates the dir and seeds the config):

- `config.json` — tuning only: sweep repos, freshness window, footer, auto-approve gate, bundle caps,
  `.submitted` retention. Seeded from `config.example.json` here. The GitHub facts are not in it: the scripts
  read the login (`WORKSPACE_GITHUB_LOGIN`), owner (`github.org`), bot list (`github.bots` + `github.review_bot`)
  and owner teams (`github.owner_teams`) through `kit_profile.py review-facts`, which names the key when one is unset.
- `ledger.jsonl` — one JSON line per (repo, pr, head) event: `surfaced`, `reviewed`, `replied`,
  `skipped`, `shadow_approve`, `shadow_fallback`, `auto_approved`. Appends take `.ledger.lock`.
- `.submitted/<digest>/` — the exact payload of every review posted (`submit-review.sh --confirm`);
  pruned after `submitted_retention_days`.
- `.scan.lock` — held by a running `pr-scan.sh`; a second sweep exits 3 instead of repeating the gh calls and racing the first for `latest` (only `--mark-only` writes the ledger, under `ledger-append.sh`'s lock).

Nothing else lives in this directory — the former `config.json` / `ledger.jsonl` / `.submitted`
compatibility symlinks were removed on 2026-09-24; every script resolves `PR_REVIEW_HOME` itself.
