# Security policy

This kit's scripts handle GitHub tokens (`gh`), Slack tokens, AWS SSO sessions and other credentials, and post to
third-party systems (chat, the tracker, PRs) on the user's behalf. A bug that leaks one of those, or that lets a
skill act as someone other than the person running it, is a security issue, not an ordinary bug.

## Reporting a vulnerability

**Report privately** — never in a public issue or PR, where the report itself could point at the problem before a
fix ships. Use [GitHub Security Advisories](https://github.com/MdaaaaO/ai-baton/security/advisories/new)
("Report a vulnerability" on the repository's Security tab). Include:

- what leaks, or what a script does that it should not (a command, a file, a diff);
- how to reproduce it (a skill name, a script, the input that triggers it);
- what you think the impact is (a token in a log, a value written where another machine could read it, an
  unintended write).

Expect an acknowledgement within a few days. There is no bug bounty; a fix ships as a normal PR once triaged, and
the advisory is disclosed after the fix is out.

## Scope

In scope: anything under `context-db/`, `skills/`, `agents/`, `hooks/`, `setup.sh`, `sync.sh` and the CI workflows
under `.github/` — in particular a token or credential written somewhere other than its documented home
(`docs/contributing.md` § Secrets), a leak-shape scanner (`leak_shapes.py`, `review_gate.py`) that misses a shape
it claims to catch, or a hook or script that runs something other than what its own file says.

Out of scope: a vulnerability in Claude Code itself, in a third-party MCP server or CLI (`gh`, `aws`, `claude`)
the kit shells out to, or in a system the kit only reads from — report those to their own maintainers.

## Supported versions

Only the latest released version is supported; there is no long-term maintenance branch. Update with
`claude plugin update ai-baton@ai-baton-kit` (plugin) or `make claude_sync` (clone).
