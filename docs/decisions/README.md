# Decisions — design choices worth not re-litigating

A decision note (ADR-style) records one design choice a contributor would otherwise reopen — in an
issue, a PR review, or a fresh conversation with the model. It is not a design doc, a changelog entry
or a how-to; those stay in `docs/*.md` and `CHANGELOG.md`. A note here answers "why is it built this
way" in one page, so the answer survives the thread that produced it.

## When to add one

Add a note when a choice is: non-obvious (a reasonable contributor would propose the alternative),
settled (the repo has moved on and reopening it costs real review time), and durable (it shapes more
than one file). Skip it for anything reversible with a one-line PR, anything already fully explained by
a doc's own prose, or a preference with no real alternative considered.

## Naming

`NNNN-kebab-title.md`, four-digit zero-padded sequence, lowercase kebab-case title — `0001-env-store.md`.
Numbers are never reused or reordered; a superseded decision gets a new number and its own note, with
the old one's Status updated to point at it.

## Fixed sections

| Section | Holds |
|---|---|
| **Status** | `Accepted`, `Superseded by NNNN`, or `Proposed` |
| **Context** | The problem the choice responds to, stated as fact — no rhetorical build-up |
| **Decision** | The choice itself, stated plainly, one paragraph or a short list |
| **Consequences** | What the decision makes easy or hard, trade-offs accepted, what would change if reversed |
| **Sources** | The files/docs the note was drawn from, so a reader can verify it against the code |

Keep a note short (roughly 60 lines) — tables over prose, no marketing language, cite paths rather than
quoting large blocks.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-env-store.md) | Environment facts live in a per-machine env store | Accepted |
| [0002](0002-sign-queue-location.md) | Where the sign-queue lives | Accepted |
| [0003](0003-bundle-only-review-runner.md) | The pr-review runner works from a bundle only | Proposed |
