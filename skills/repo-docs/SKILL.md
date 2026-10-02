---
name: repo-docs
description: Write or restructure a repo's README.md and CONTRIBUTING.md in the house style — badges, a short pitch, code first, tables over prose, detail linked to docs/ — measured by readme-check.py. Use when a README reads as a wall of text or a repo goes public. Not for docs/ pages or CHANGELOGs.
user-invocable: true
argument-hint: "<repo-dir> [readme|contributing|both]"
metadata:
  version: "3"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
---

# repo-docs — a front page a stranger understands in one screen

A README answers four questions in this order, and nothing else: **what is it, why would I want it, how do I
start, where is the rest.** Everything a reader needs only after deciding to use the project lives in `docs/`.
CONTRIBUTING answers **how do I get a change merged** — the short version first, the rules by link.

Run the checker before and after; its numbers, not an impression, say whether the page is done:
`python3 $BATON/skills/repo-docs/readme-check.py <file>` (`ok` / `warn` / `FAIL` per rule, exit 1 on a FAIL).

## 1. Measure and inventory

1. Run `readme-check.py` on the current `README.md` and `CONTRIBUTING.md`; keep the output for the PR body.
2. Read the repo's own facts — never invent them: the CI workflow file names (`.github/workflows/*.yml`, the badge
   URL needs the exact file), the license file (`LICENSE`, or none — then there is no license badge and § License
   says so), the package registry name if published, the install path(s) that really work, the docs that exist
   under `docs/`. Run the install command in a scratch dir when it is cheap to do so.
3. Sort every existing paragraph into **keep** (answers one of the four questions), **move** (true but reference —
   name the `docs/` page it goes to, existing or new) or **drop** (history, rationale for a past decision, anything
   the code or git log already records).

## 2. The README skeleton

In this order; a section that does not apply is left out, not padded.

| # | Block | Rule |
|---|---|---|
| 1 | `# <name>` + tagline | The project name, then one bold or italic sentence under it — no paragraph before it. A logo or a hero screenshot/GIF only if one exists and shows the payoff. |
| 2 | Badge row | 3–6 badges, one per line, plain flat shields (never `for-the-badge`): CI status, version/release, license first; then language versions, coverage, docs, "PRs welcome" only where that surface exists. Every badge links somewhere real. Shapes: `references/badges.md`. |
| 3 | Pitch | 1–3 sentences, ≤ 80 words: what it is and what it does for the reader. Concrete nouns, no "powerful", "seamless", "simply". |
| 4 | First code block | Within the first screen (≤ 40 lines): the install command or the smallest real use, with its output when that is the point. |
| 5 | Why / comparison | Optional. A table of the alternatives, one row each, plus one honest sentence on when to use one of them instead. |
| 6 | How it works | A numbered list of ≤ 6 steps, or one Mermaid diagram — never both saying the same thing. |
| 7 | Install / Quick start | Every supported path as its own short code block; prerequisites as a one-line list. |
| 8 | Usage / What's inside | Items grouped by category, each `**name** — one clause` (a list), or a table of item *types* → purpose. More than ~15 items → the full list moves to `docs/` and the README links it. |
| 9 | Documentation | Links to the `docs/` pages, one line each. |
| 10 | Contributing | One sentence and a link to `CONTRIBUTING.md`. |
| 11 | License | One line naming it, linking `LICENSE`. |

Limits `readme-check.py` enforces: ≤ 1,500 words in all, pitch ≤ 80 words, first code block by line 40, no
paragraph over 150 words (aim for ≤ 90), no table row over 400 characters, a section over 350 words is a warning
(split it or move it), an install and a license section (or an install command in the first code block).
Every install path gets its own subheading and code block (per package manager, per harness) — a `<details>` block
hides a secondary one. Past ~150 lines, add a table of contents under the pitch.

## 3. The CONTRIBUTING skeleton

1. One line of thanks and **The short version** — 3–6 bullets: open an issue first for anything non-trivial; the
   commit / PR title convention; the one command that must pass locally (`make ci` or its equivalent); tests come
   with behaviour. This list alone must be enough to get a typical PR merged.
2. `## Use of AI` — where the project takes AI-assisted contributions, say what it expects in two or three lines
   (the contributor understands and has run what they submit; disclose the tool when it wrote substantial parts)
   or link the policy. Say what the project actually does — never invent a policy.
3. `## Development setup` — the commands, as code blocks.
4. `## Pull requests` — what CI checks, what review looks like, how merging happens; each rule one line, with a
   link where the full rule lives.
5. Optional `## Releasing (maintainers)`, `## Code review` — short; the long form is a `docs/` page.

≤ 800 words in all. A rule that needs more than a paragraph is a `docs/` page that CONTRIBUTING links.

## 4. Style — every line

- **Lead with the point.** First sentence of a section says what the reader gets; the reason comes after, if at all.
- **Second person, present tense, active voice.** "Run `make ci`", not "`make ci` should be run".
- **Sentence-case headings** ("Quick start", not "Quick Start"); numbered lists only for steps that happen in order.
- **Alt text on every image**; a screenshot or GIF earns its place only when it shows the result.
- **Lists and tables over paragraphs** whenever there are three or more parallel items.
- **Short source lines** (wrap near 110 characters) so diffs stay reviewable; no line of prose over 200.
- **GitHub alerts sparingly** — the modern form of a bold "Note:": at most two `> [!NOTE]` / `> [!WARNING]` blocks, for things that bite.
- **No emoji in headings, no marketing adjectives, no "simply" / "just" / "easy".**
- **Links, not repetition**: say a rule once, where it lives; everywhere else links it.

## 5. Write, verify, open the PR

1. Edit in the repo's worktree so the user can watch the files change; `docs/` pages first (so the README's
   links resolve), then `README.md`, then `CONTRIBUTING.md`.
2. Re-run `readme-check.py` on both files until no FAIL remains; each `warn` is fixed or justified in the PR.
3. Check every relative link resolves and every badge URL answers (`curl -sfo /dev/null -w '%{http_code}' <url>`).
4. Open the PR (`pr-open`) with the before/after checker output in the body, and a note of what moved where.
