#!/bin/sh
# scaffold_script (case.yaml context.scaffold_script, --scaffold only): runs before Claude starts, outside the
# sandbox, in the empty scratch workspace. Writes a small context store under ./store with one finding of each
# mechanical kind (an orphan, a dead link, a stale tracker claim, a doc past the archive rule) plus history lines
# that must NOT be flagged, and ./fakegh, a stand-in for `gh` that answers the batched GraphQL query offline.
set -eu

mkdir -p store/proj store/archive

cat > store/proj/widgets-project.md <<'DOC'
---
title: Widgets
type: epic
domain: proj
status: active
updated: 2026-10-20
---

# Widgets

## Tracker & links

- Epic: https://github.com/acme/widgets/issues/1 is open
- Notes: [design notes](design.md), [runbook](runbook.md), [spike](old-spike.md)
- 2026-09-01 — https://github.com/acme/widgets/issues/3 open (a dated history bullet)

## Goal

Ship the widgets.

## Session log

- 2026-09-02 — https://github.com/acme/widgets/issues/4 open (the log records history)
DOC

cat > store/proj/design.md <<'DOC'
---
title: Design notes
type: reference
domain: proj
status: active
updated: 2026-10-20
---

# Design notes

Back to the [epic](widgets-project.md).
DOC

cat > store/proj/loose-notes.md <<'DOC'
---
title: Loose notes
type: reference
domain: proj
status: active
updated: 2026-10-20
---

# Loose notes

Nothing refers to this document.
DOC

cat > store/proj/old-spike.md <<'DOC'
---
title: Old spike
type: epic
domain: proj
status: closed
updated: 2026-01-05
---

# Old spike

Closed long ago; see the [epic](widgets-project.md).
DOC

cat > fakegh <<'SH'
#!/bin/sh
# Offline stand-in for `gh api graphql`: acme/widgets#1 is CLOSED, #3 and #4 are CLOSED too.
printf '%s' '{"data":{"repository":{"n1":{"__typename":"Issue","state":"CLOSED"}}}}'
SH
chmod +x fakegh
