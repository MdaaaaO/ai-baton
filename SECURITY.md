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
`claude plugin marketplace update ai-baton-kit && claude plugin update ai-baton@ai-baton-kit` (plugin —
refresh the marketplace first, or the update can stay on the old release) or `make claude_sync` (clone).

## Verifying a release

Every release after v0.7.0 carries a manifest: `commit <sha>` plus one sha256 line per git-tracked file at that
commit, built by `context-db/bin/release_manifest.py` and uploaded as the `manifest.txt` release asset. The
`release` workflow attests that manifest with a build-provenance attestation (Sigstore, signed with the job's own
OIDC token — no secrets) before the release is published, so such a release never exists without one. v0.7.0 and
the releases before it were published before the workflow built a manifest: they have no `manifest.txt` asset,
and the commands below have nothing to check for them.

Check a downloaded release:

```sh
gh release download <tag> --repo MdaaaaO/ai-baton --pattern manifest.txt
gh attestation verify manifest.txt --repo MdaaaaO/ai-baton \
  --signer-workflow MdaaaaO/ai-baton/.github/workflows/release.yml
```

A pass proves the manifest was signed by this repository's `release` workflow and has not changed since. It
does not say which run or which tag: the manifest's own `commit` line does. Run from a checkout of the kit,
`python3 context-db/bin/release_manifest.py verify manifest.txt --root <checkout of that tag>` then checks the
manifest's claims against that checkout — every tracked file's hash, and the commit.

Immutable releases and a tag ruleset on `v*` (no update, no delete) are both on for this repository. The
ruleset covers every `v*` tag, old and new: a tag cannot be moved or deleted. Immutability covers a release
published while the setting is on, which is every release after v0.7.0: its assets cannot be replaced
afterwards, so the release you verify today is the one anyone fetches tomorrow. v0.7.0 and the releases before
it are not immutable. Neither setting says the release is good: nothing here says the code behind a release is
free of bugs or was reviewed, only where it came from and that it has not changed since.

A clone's sync verifies a held release tag before applying it (a clone on `kit.channel main` follows
`origin/main` and verifies nothing). The check running and *failing* — `gh
attestation verify` rejects the manifest, or the manifest names a different commit than the tag — refuses the
update: nothing is applied. The check being unable to *run* at all — no `gh` on `PATH`, an unauthenticated or
too-old `gh`, no answer from GitHub, a release with no `manifest.txt` asset (the full list:
[`docs/sync.md`](docs/sync.md)) — still applies the release (a clone must be able to catch up with no
network), and records the sync as `unverified` in `.sync-status` and, durably, in `.sync-unverified`;
`kit-health` § 1 reads the latter and warns, naming the command above to run by hand. The mark stays until
the release verifies by hand, a later release replaces it, or `HEAD` moves to a different commit — not just
until the next sync run.

There is no way yet to pull back a release that turns out to be bad: the remedy today is a newer release.
