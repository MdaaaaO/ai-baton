# Knowledge lint — does the store still say what is true?

Owner decision, recorded on the design ticket: the lint is a **mode of `kit-health`** (`/kit-health lint`), not a
skill of its own, and it adds no description bytes. A script finds what a machine can find; a few no-tool forks
read what only a reader can judge; the main session walks every finding in one carousel and applies only the
accepted ones through the ctx tools.

## What is a claim

A claim is read only from a **current-state section** of a live doc: `Tracker & links`, `Goal`, `Open PRs` and
the **newest** bullet of `Remaining work`. Everything else is history and is never linted for staleness: the
`Session log`, `archive/`, docs of `type: log`, `status: archived` docs, and any dated bullet (`- 2026-09-01 — …`)
in any section. A history line is true of its day; flagging it would only teach the walker to dismiss findings.

## The script

`python3 $BATON/context-db/bin/ctx_lint.py check` is read-only, stdlib only, and prints one finding per line or
`CLEAN` (flags and exit codes: `docs/engine-cli.md`).

| Line | Meaning |
|---|---|
| `ORPHAN <doc>` | no inbound link or path mention from any doc, a generated index (`INDEX.md`, `SESSION_INDEX.md`) not counting |
| `DEAD <doc> <target>` | an internal Markdown link that resolves to nothing |
| `STALE <doc> <claim> · <system of record>` | a tracker key stated in a current-state section whose state the tracker contradicts |
| `AGED <doc> <why>` | a closed or done doc untouched past the archive rule (`gen_sessions.ARCHIVE_DAYS`) |
| `UNCHECKED <doc> <claim>` | a tracker claim this run could not read (offline, failed read, over the cap, key not found) |

Tracker states come from **one batched GraphQL query per repository** through `gh api graphql`, capped at
**100 distinct keys per run**. Past the cap, or when a repository's read fails, the claim is `UNCHECKED`: the
script never guesses a state. `--offline` (or `CTX_LINT_OFFLINE=1`) skips `gh` entirely and every tracker claim is
`UNCHECKED`; the mechanical checks (orphans, dead links, aged docs) still run.

Exit codes: 0 clean or only `UNCHECKED`, 1 a `gh` read failed and nothing else was found, 2 bad usage,
3 at least one finding.

## The forks

Prose-stated facts and contradictions between two docs need a reader. `ctx_lint.py plan` lists the domains to
read: those with a doc changed since the **lint stamp**, plus the domains the last run left `not read`.

- One **haiku** fork per domain; **sonnet** only for a domain whose bundle is over 60 KB.
- At most **6 forks per run**; the remaining domains are listed `not read` and carried in the stamp.
- The fork's input is `ctx_lint.py bundle <domain>`: the domain's live docs, `=== <path>` before each. No logs,
  no archive. A fork gets no repo, no transcript and no tool; the harness cannot strip tools, so the restriction
  is by instruction, as in [`cold-reader.md`](cold-reader.md).

Call `Agent` with `model: haiku` (or `sonnet`), `subagent_type: general-purpose`; paste the bundle in place of
`<docs>` and hand it exactly this prompt:

```
Read the following context-store documents. Report only what the text itself shows, one line each, in exactly
this grammar and no other prose:
STALE <doc> <claim> · <system of record>   a fact in a current-state section (Tracker & links, Goal, Open PRs,
                                           newest Remaining work bullet) that a later line in the same text
                                           shows is no longer true; name where the truth lives
CONTRADICTS <doc a> <doc b> <what>         two docs state incompatible facts about the same thing
ORPHAN <doc>                               a doc no other doc in this text refers to
UNCHECKED <doc> <claim>                    a claim you cannot judge from this text alone
Never judge a session log, a dated bullet or an archived doc: they are history. If nothing is wrong reply with
exactly one line: CLEAN. Answer from this text alone — use no tool, read nothing else.

<docs>
```

The forks cannot see the tracker, so the main session **verifies every fork `STALE` and `CONTRADICTS` line
against its system of record** (`gh issue view`, `git log`) before the carousel, and drops any line the record
contradicts. A line that does not parse as the grammar above is dropped, not repaired.

## The stamp

`.context/kit-health/lint-<env>.md` (`type: log`, `status: reference`) holds two frontmatter fields:
`last_lint: <ISO timestamp>` and `not_read: [<domain>, …]`. The script only **reads** it. The session step writes
it, with the ctx tools, after the carousel: create it once with `make -C $BATON/context-db new TYPE=log
DOMAIN=kit-health SLUG=lint-<env>`, then `ctx_fm` the two fields. A missing or malformed stamp means never linted:
every domain is planned.

## The carousel

All findings, mechanical and verified-fork, go in one carousel ([`carousel.md`](carousel.md): at most 4 questions
per call, 2-4 options, recommended first, each option's consequence stated; trivial findings batched). Options are
**Fix** (the doc edit, stated), **Ticket** (`ticket-open`, for a finding that needs work, not an edit) and
**Skip**. `UNCHECKED` and `not read` lines are reported in prose, never asked.

Only accepted fixes are applied, each through the ctx tools (`ctx_str_replace`, `ctx_fm`, `ctx_move`) — never a
direct write of a `.context/` doc — and each leaves one line in that doc's `Session log` naming the finding it
resolved. A finding the walk skips changes nothing. A fix applied before the carousel has answered is a
protocol break, however obviously right the fix.

## When it runs

`/kit-health lint`, on request. `session-retro` offers it in one line when the session touched three or more
docs of the store. It is never scheduled and never runs inside a health run.
