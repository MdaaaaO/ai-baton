# 0002 — Where the sign-queue lives

## Status

Accepted

## Context

Signing happens on the user's machine ("the host") because `commit -S` fails wherever the signing key is
absent, and a session cannot hand the user a chained git one-liner — long commands break on the user's
terminal line wrap. So a session **enqueues** a signing job and the user **drains** the queue with one
command (`make sign`). Enqueued jobs are plain `sh` scripts that must survive between the session that
queued them and whenever the user next runs `make sign`, and they must not disappear on a routine kit
update.

## Decision

The queue lives at `.context/state/sign-queue/` — inside the workspace's `.context/`, not under the kit
(`.claude/`). `skills/sign-queue/signq.py` resolves the directory as `SIGN_QUEUE_DIR` env override, else
`<context root>/state/sign-queue`, and treats a kit-relative `sign-queue/` directory as a legacy location
it migrates jobs out of on the next run. Nothing in the kit ships or seeds the directory; `enqueue.sh`
creates it on first use. Job logs live alongside it, at
`.context/state/sign-queue/logs/<job>.log`.

## Consequences

- A plugin update (which replaces the plugin cache) or a fresh kit clone never deletes a queued or
  in-flight job, because the queue is not part of the kit's own tree.
- The queue is per-workspace, not per-repo: one `make sign` on the host drains jobs from every repo a
  session queued into, using the overview table's `REPO` column to tell them apart.
- A machine that upgrades from before this decision carries a `.claude/sign-queue/` directory; `signq.py`
  moves its files into the current location automatically rather than requiring a manual step, and warns
  on a name collision instead of overwriting silently.

## Sources

- `skills/sign-queue/signq.py` lines ~42–55 (`KIT`, `Q`, `LEGACY_Q` — the location and the (#7) migration
  comment) and the `migrate`-on-run logic
- `skills/sign-queue/README.md` (**Needs** / queue location paragraph)
- `skills/sign-queue/SKILL.md` § User side — drain (job-file location, `logs/`) and § Why (the owner
  decision behind the queue's existence, 2026-09-10)
- `skills/signed-git-commits/SKILL.md` (defers the signing/push hand-off to `sign-queue`)
