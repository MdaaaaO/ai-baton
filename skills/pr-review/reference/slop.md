# slop.md — AI-residue lens (read-only on other people's PRs)

Flag only lines the PR adds, only when the pattern is absent from the rest of the file, and only
as `nit:`. Never edit another author's code; quote the concrete replacement.

**Code**
- Comments that restate the code (`# loop over rows`, `# return result`), section banners in a file that has none.
- Docstrings that echo the signature without behaviour, invariants or failure modes.
- Defensive `try/except` or null checks around trusted paths no other caller guards.
- Casts to `Any`/`interface{}` to silence a type problem.
- Style that breaks the file's conventions (quotes, import order, assertion library).
- Emoji in code, logs or error strings when the file has none.
- Multi-paragraph "future work" scaffolds; a one-line `TODO:` is fine.
- Review-history residue: ticket numbers, "as requested by reviewer", "now/previously" narration in comments, test names or headings. Stranger test: keep only wording that describes the shipped code to a reader with no review context. Keep a ticket link when it documents a live upstream constraint.

**Prose in the diff** (README, docs, dbt descriptions, PR body when the repo stores it)
- Heading scaffolds on short content, "First… Next… Finally…" signposting, qualification-stuffed parentheticals, process framing ("after thorough analysis…").
- Bullet lists that are one thought split three ways.

**Out of scope**: formatter-level style, renames for taste, verbose-but-correct code, comments that document a genuine gotcha. When in doubt, leave it.
