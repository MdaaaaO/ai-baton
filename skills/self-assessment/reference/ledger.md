# Ledger update — the four sub-steps behind step 10

Loaded from `SKILL.md` step 10 (`self_assessment.ledger` on). Continues after
`python3 $BATON/context-db/bin/kit_profile.py get self_assessment.ledger_url`:

1. **URL set** → `Artifact read` with that `url` and `path: index.html` (saves the live page locally), then
   `python3 $BATON/skills/self-assessment/ledger.py build --page <saved index.html> --week <the week file
   step 5 wrote> [--week …] --out <scratch>/ledger.html`. It replaces the card
   with the same id (a re-run updates in place), appends a new one, sorts by week and refreshes the masthead
   count + date range. Republish **with `url`** = the configured URL and the same `file_path` — publishing
   without `url` creates a duplicate page.
2. **URL empty** (first run on this machine) → `ledger.py build --init --week <every week file in the domain> --out
   <scratch>/ledger.html --title "Update Ledger" --eyebrow "<team · user>" [--report-url <self_assessment.report_url>]`
   (template `ledger-template.html` next to the script), publish it as a new private Artifact (icon
   `calendar`), then `python3 $BATON/context-db/bin/kb.py config-set self_assessment.ledger_url <url>` so
   no later run creates a second page. The store is local — every environment gets its own ledger.
3. `ledger.py check <html>` runs inside `build` (unique ids, ≥3 sections per card, no local paths in
   bullets, no unfilled placeholders); on a hit nothing is written to `--out` (the page lands in
   `<out>.rejected` for inspection) and the build exits non-zero — fix the week file, not the page.
4. The card's `tag` derives from the week file's frontmatter: the sprint from the title, `composed <date>,
   week in progress — re-check after the Monday re-run` while `status: active`, `back-filled <date>` when
   `provenance: back-fill` is set (reference/backfill.md). The final reply **links the ledger
   page and still pastes the block** — the page is the archive, the block is what the user pastes today.
