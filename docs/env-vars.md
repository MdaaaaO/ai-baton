# Environment variables — every one the kit reads

One reference table per group, built from a scan of every tracked `.py`, `.sh`, `Makefile`/`*.mk` and
`hooks.json` file for `os.environ.get`/`os.getenv` and the shell `${NAME:-…}`/`${NAME:?…}`/`${NAME:+…}`
family — never hand-maintained (`context-db/tests/test_env_vars.py` runs the same scan and fails the
build the day a script reads a name that is not a row here). **Scope** is one of:

- **user-facing** — a knob a person sets to change behaviour (a timeout, a flag, an identity value).
- **internal** — plumbing between two of the kit's own processes, or an install-path override most
  machines never touch.
- **test-only** — read only by the eval/CI harness, never by a person.

A platform variable (`HOME`, `TMPDIR`, `CI`, `GITHUB_*`, `RUNNER_*`, `CLAUDE_*` from Claude Code,
`NO_COLOR`) carries no row: it is not the kit's to document. `WORKSPACE_*` identity variables are
listed below for completeness, but their single source of truth is `docs/packaging.md` § Identity and
`context-db/bin/kit_profile.py`'s `IDENTITY_KEYS`.

## Sync, session registry, evals

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `SYNC_FETCH_TIMEOUT` | `60` (seconds) | `sync.sh` | how long the SessionEnd hook's `git fetch` may run before `sync.sh` gives up | user-facing |
| `SYNC_LOCK_WAIT` | `30` (seconds) | `sync.sh` | how long one `sync.sh` waits for another's `flock` before giving up | user-facing |
| `SESSION_INDEX_MAX_ENDED` | `5` | `context-db/bin/gen_sessions.py` (exported by `context-db/Makefile` from `make … MAX_ENDED=<n>`) | how many ended sessions still show as rows in `SESSION_INDEX.md` | user-facing |
| `SESSION_ARCHIVE_DAYS` | `7` | `context-db/bin/gen_sessions.py` | how many days an ended session stays in the live index before it moves to `archive/` | user-facing |
| `SESSION_ARCHIVE_NOPROMPT_HOURS` | `48` | `context-db/bin/gen_sessions.py` | how many hours after ending a session archives without a confirmation prompt | user-facing |
| `SESSION_STATS_PRICES` | the engine's built-in per-model table | `context-db/bin/session_stats.py` | override token prices as `in,cw,cr,out[,cw_1h]`, for a model the built-in table does not price | user-facing |
| `EVAL_MIN_CASES` | `10` | `context-db/bin/eval_check.py` | minimum eval-suite case count before `eval_check.py` stops warning | test-only |
| `EVAL_MIN_EACH` | `3` | `context-db/bin/eval_check.py` | minimum near-miss count per eval case before `eval_check.py` stops warning | test-only |

## Scratch space and paths

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `CONTEXT_ROOT` | walk-up discovery (`docs/layout.md` § Content root) | `context-db/bin/kit_profile.py` (every script that asks `context_root()`), `skills/kit-health/kit-health.py` | override where the `.context/` document DB lives | user-facing |
| `KIT_SCRATCH` | computed per-session scratch dir | `context-db/bin/kit_profile.py` (`scratch()`) | override the per-session scratch directory outright | user-facing |
| `XDG_RUNTIME_DIR` | unset (falls back to `TMPDIR`/`/tmp`) | `context-db/bin/kit_profile.py` (`scratch()`) | the standard XDG per-user runtime dir; used as the scratch root's parent when set | internal |
| `XDG_CACHE_HOME` | `~/.cache` | `context-db/bin/kit_profile.py` (`scratch(stable=True)`), `context-db/bin/ctx_adapter.py` (the pinned ctx-store install) | the standard XDG cache dir; parent of the stable (per-user, cross-session) scratch dir and of the pinned ctx-store copy | internal |
| `PROJECTS` | the computed workspace root (clone: kit's parent; plugin: `CLAUDE_PROJECT_DIR` or `$PWD`) | `setup.sh` | override the workspace root `setup.sh` installs into | user-facing |

## ctx-store adapter (`context-db/bin/ctx_adapter.py`)

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `KIT_CTX` | unset (the pinned install under `${XDG_CACHE_HOME:-~/.cache}/ai-baton-kit/ctx-store/<tag>/`) | `context-db/bin/ctx_adapter.py` | a `ctx` executable to use instead of the pinned install; set but not an executable file means "not installed", never a fallback | user-facing |
| `CTX_STORE` | unset (the hooks name the content root with `--store`) | `context-db/bin/ctx_adapter.py` | ctx-store's own store locator; when set, the hooks leave it to ctx instead of naming the content root | user-facing |
| `KIT_NO_CTX_FETCH` | unset | `setup.sh` | `1` skips `ctx_adapter.py install`'s network git clone of the pinned ctx-store tag and only prints the manual install/adopt commands, as setup.sh always did before; set by the setup tests so none of them clones from the network | user-facing |

## `context-db/bin/new.sh` (`make new …`)

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `TYPE` | none (required) | `context-db/bin/new.sh` | the context-doc type to scaffold (`epic`, `reference`, `repo`, …) | user-facing |
| `SLUG` | none (required) | `context-db/bin/new.sh` | the new doc's kebab-case file slug | user-facing |
| `DOMAIN` | per-type default (e.g. `meetings`), else empty | `context-db/bin/new.sh` | the `.context/` domain folder the doc is placed under | user-facing |
| `TITLE` | `SLUG` | `context-db/bin/new.sh` | the doc's frontmatter title | user-facing |

## `sign-queue`

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `SIGN_QUEUE_BY` | `WORKSPACE_USER`, else `?` | `skills/sign-queue/enqueue.sh` | who a queued commit job is attributed to | user-facing |
| `SIGN_QUEUE_SKIP_STYLE` | `0` | `skills/sign-queue/enqueue.sh` | `1` skips the commit-style check for one deliberate one-off enqueue | user-facing |
| `SIGN_QUEUE_JOB_TIMEOUT` | `300` (seconds) | `skills/sign-queue/signq.py` | how long `run_job` lets one job's `git`/`gh` chain run before killing its whole process group | user-facing |
| `SIGN_QUEUE_ROOT` | unset | `skills/sign-queue/signq.py` | override the workspace root the queue resolves everything else from (a plugin install's own workaround) | internal |
| `SIGN_QUEUE_CONTEXT` | `SIGN_QUEUE_ROOT/.context`, else `kit_profile.context_root()` | `skills/sign-queue/enqueue.sh`, `skills/sign-queue/signq.py` | override which `.context/` the queue lives under | internal |
| `SIGN_QUEUE_DIR` | `<context>/state/sign-queue` | `skills/sign-queue/enqueue.sh`, `skills/sign-queue/signq.py` | override the queue directory itself | internal |

## `pr-review`

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `PR_REVIEW_HOME` | `<context>/state/pr-review` | `skills/pr-review/scripts/fetch-context.sh`, `reply-threads.sh`, `submit-review.sh`, `trivial-check.py`, `skills/pr-scan/pr-scan.sh` | override pr-review's state directory (a plugin install's script location is Claude Code's cache, wiped on update) | internal |
| `PR_REVIEW_AUTO` | `0` | `skills/pr-review/scripts/submit-review.sh` | `1` takes the trivial-PR auto-approve path (still refused unless `auto_approve.mode == "live"`) | internal |
| `PR_REVIEW_AUTO_COMMENT` | `0` | `skills/pr-review/scripts/submit-review.sh` | `1` takes the unattended auto-COMMENT path for direct review requests (still refused unless the event is `COMMENT`, `auto_comment.mode == "live"`, and the PR is not the user's own) | internal |
| `PR_REVIEW_ALLOW_CLOSED` | `0` | `skills/pr-review/scripts/submit-review.sh` | `1` allows posting a review on a merged/closed PR (never APPROVE/REQUEST_CHANGES there) | internal |
| `PR_REVIEW_FETCH_CONCURRENCY` | `6` | `skills/pr-review/scripts/fetch-context.sh` | how many blob fetches `fetch-context.sh` runs at once | user-facing |
| `PR_REVIEW_FETCH_RETRY_DELAY` | `2` (seconds) | `skills/pr-review/scripts/fetch-context.sh` | base backoff before retrying a rate-limited blob fetch (tests set it near 0) | test-only |
| `PR_SCAN_OUT` | `kit_profile.py scratch --stable pr-scan` | `skills/pr-scan/pr-scan.sh` | override pr-scan's output directory (shared across sweeps by default) | internal |

## `pr-watch`

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `PR_WATCH_BOT_LOGIN` | `github.review_bot` from the env store | `skills/pr-watch/pr-watch.sh`, `bot-verdict.sh`, `pr-merge.sh`, `skills/pr-review/scripts/trivial-check.py` | override the review bot's login for this run | user-facing |
| `PR_WATCH_SELF` | the resolved GitHub identity (`gh api user`, else `WORKSPACE_GITHUB_LOGIN`) | `skills/pr-watch/pr-watch.sh` | override this session's own GitHub login | user-facing |
| `PR_WATCH_SYNC` | `1` | `skills/pr-watch/pr-watch.sh` | `0` disables keeping a waiting PR's branch synced with its base | user-facing |
| `PR_WATCH_SYNC_COOLDOWN` | `3600` (seconds) | `skills/pr-watch/pr-watch.sh` | how often a fast-moving base may re-trigger CI on the PR | user-facing |
| `PR_WATCH_KNOWN_RED` | empty | `skills/pr-watch/pr-watch.sh` | extended regex of failing-check names to mute as already-known-red | user-facing |
| `PR_WATCH_REPLAY` | `0` | `skills/pr-watch/pr-watch.sh` | `1` re-emits the current bot verdict / CHECK NOT GREEN on start even when already reported | user-facing |

## Identity (`WORKSPACE_*`)

One reader, `context-db/bin/kit_profile.py`'s `identity()` (plugin option first, then the variable
itself — settings.local.json env or a shell export); full spec `docs/packaging.md` § Identity.

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `WORKSPACE_USER` | empty | `kit_profile.identity()` (sign-queue, cost-report, self-assessment ledger, kit-health) | the user's display name | user-facing |
| `WORKSPACE_GITHUB_LOGIN` | empty | `kit_profile.identity()` (pr-watch, cost-report, kit-health) | the user's GitHub login | user-facing |
| `WORKSPACE_TZ` | `tz_default` from the env store, else UTC | `kit_profile.identity()` / `kit_profile.tz()` (session registry, session-stats, self-assessment) | the IANA zone timestamps render in | user-facing |
| `WORKSPACE_SLACK_SELF_DM` | empty | `kit_profile.identity()` (slack-draft) | the user's own chat DM channel id, where drafts park | user-facing |
| `WORKSPACE_SLACK_LATTICE_DM` | empty | `kit_profile.identity()` (self-assessment, kit-health) | the performance-review bot's DM channel id | user-facing |

## CI-only

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `PR_TITLE` | empty | `.github/scripts/check-pr-issue.sh` | the PR title, passed in by the pr-issue CI check | internal |
| `PR_BODY` | empty | `.github/scripts/check-pr-issue.sh` | the PR body, passed in by the pr-issue CI check | internal |
| `PR_AUTHOR` | empty | `.github/scripts/check-pr-issue.sh` | the PR author's login, passed in by the pr-issue CI check | internal |
| `GH_TOKEN` | unset (falls back to the local `gh` login) | `.github/scripts/check-pr-issue.sh`, `skills/kit-health/kit-health.py` | the token `gh` calls authenticate with; also how kit-health tells a sandbox proxy-token machine from a normal login | internal |
| `HEARTBEAT_DETACHED` | unset | `skills/session-register/heartbeat.sh` | internal flag the script sets on its own detached re-exec, so the child knows not to fork again | internal |

## Third-party version pins — `.github/versions.env`

Not read through `os.environ`/`${NAME:-…}` in a script; a workflow step sources the file
(`. .github/versions.env`) and then uses the bare name (`$CLAUDE_CODE_VERSION`). One pin per tool,
bumped only there (`docs/contributing.md` § Releases); checked by
`context-db/tests/test_ci_hygiene.py`'s `VersionsSinglePlace` and the parity test below.

| Name | Default | Read by | Purpose | Scope |
|---|---|---|---|---|
| `CLAUDE_CODE_VERSION` | `2.1.283` | `ci.yml`, `evals.yml`, `tree-review.yml` | the Claude Code CLI version those workflows install | internal |
| `CONVENTIONAL_RELEASE_VERSION` | `0.3.1` | `release.yml`, `pr-title.yml`, `workspace.mk` (`kit_release`) | the exact `conventional-release` pin (never a floating range) | internal |
| `PYYAML_VERSION` | `6.0.3` | `ci.yml` (the two jobs running `make … test`) | a real YAML parser to cross-check frontmatter against, alongside the kit's own lenient one | internal |
