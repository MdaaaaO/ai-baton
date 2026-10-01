# Verified — the three accepted shapes, and what `verified.py` checks

Loaded from `SKILL.md` § Verified. `verified.py` (this directory) is the mechanical half — `run` fills the
block, `check` refuses a bare claim and a stale recorded head. It reuses `evidence_check.py`'s evidence-shape
regexes (`CLAIM_SEP`, `has_evidence` — `docs/engine-cli.md` documents that tool's own CLI) rather than
re-implementing them; neither tool asserts a claim is *true*, only that it carries real output, a URL, or a
PR/commit reference instead of a bare assertion.

## The three shapes a `## Verified` section may hold

**1. A declared command ran (`verify.repos.<owner/repo>.cmd` is set).** `verified.py run` prints this
verbatim; paste it under the `## Verified` heading as-is:

```
Command: `make -C context-db ci` (timeout 300s)
Head: `a1b2c3d4e`
Exit: `0`

```
<last 15 lines of stdout+stderr, colour codes stripped, ≤ 1200 bytes>
```
```

A non-zero `Exit:` (or `Exit: `timeout``) is not hidden or retried — `pr-open` opens the PR as a draft, keeps
the failing tail, and says so in its one-line report. `check` refuses this shape only when the fenced block
is empty — a `Command:`/`Exit:` pair with no pasted output is the same bare claim as free prose, just dressed
up.

**2. No declared command.** Cite every command the session actually ran, one line each, in
`evidence_check.py`'s own `` `cmd` → `output` `` shape:

```
## Verified
- `pytest -q` → `12 passed in 1.03s`
- `ruff check .` → `All checks passed!`
```

Any of `evidence_check.py`'s other evidence shapes also passes a line (a URL, a `#<n>` / `owner/repo#<n>`
reference, a 7–40-char hex commit with at least one digit and one letter) — `check` feeds each non-blank line
to `has_evidence` as a fake `cited · <line>` claim, so the shape rules live in exactly one place.

**3. Nothing ran.** `Not verified locally: <reason>` — one line, a real reason (no network access here, the
repo has no test suite yet, …). This is an honest limit, not a claim, so `check` never asks it for evidence.

Anything else — "tests pass", "looks good", a checklist with no output — is a bare claim. `check` names it on
stderr, `verified: <line>: <claim>`, exit 3, the same style `evidence_check.py` uses for a bare `**Verified**`
claim in a ticket comment.

## `check`'s two gates

```
verified.py check <body-file | -> [--pr <owner/repo> <n>]
```

1. **Bare claim.** Every line in the `## Verified` section (shape 2/3 above) or the fenced tail (shape 1)
   carries evidence; a body with no `## Verified` heading at all passes too — this tool checks claim shape,
   never whether the section exists.
2. **Stale head** (only with `--pr`, only when the section carries a `Head:` line — shape 1). Compares the
   recorded sha's first 9 characters against `gh api repos/<owner/repo>/pulls/<n> --jq .head.sha`'s own first
   9 — `pr-merge.sh`'s `head_of` display width. A mismatch prints `STALE <old> → <new>`, exit 3: the worktree
   moved since the command ran, so the pasted output no longer speaks for the pushed head. Re-run `verified.py
   run` and re-paste, the same turn the diagram check's `DRIFT` would get fixed in.

## Optional sections

`## Not in this PR` and `## One honest limit`, each at most 3 lines, directly after `## Verified`. Neither is
checked by `verified.py` — they are prose, not evidence claims.
