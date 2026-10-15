# pr-scan — auto-approve and auto-COMMENT detail

Loaded from `SKILL.md`. Each section below is the full text moved out of the body section it backs.

## Trivial-PR auto-approve

The contract lives in one place: `pr-review/reference/auto-approve.md`.

## Unattended auto-COMMENT (owner-decision scope, opt-in — off by default)

Rows this fork flags `C` (config block `auto_comment` in `config.json`: `mode` off/shadow/live, `prios` — direct
requests (`[1]`) by default, `max_per_tick`) are eligible for `pr-review`'s unattended auto-COMMENT path — a
deterministic, gh-call-free pre-filter computed inline in `pr-scan.sh` alongside each row (prio in `prios`, `kind`
not `follow_up`, author not a bot, capped at `max_per_tick` rows). A direct re-request on a PR already reviewed
(`kind=re_review`) is excluded by default — the prior review's prio alone does not carry a re-request onto the
trailer; `auto_comment.include_re_review: true` opts it back in. This fork only computes and reports eligibility
in the `**AUTO-COMMENT**` trailer — it never spawns a runner or decides anything; the full contract (what the main
session does with an eligible row, the `on_stop` policy, the ledger status) is `pr-review/SKILL.md` § Unattended
auto-COMMENT path for direct review requests.
