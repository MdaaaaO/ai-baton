---
name: mission-control
description: "Builds and opens the private mission-control page: the store's sessions, spend, doc catalog, priorities, queues and pointers on one local HTML file. Use when asked to open, show or rebuild the mission-control page, where the mission page is, or for an overview of sessions, spend and the store. Not for session-register, cost-report, kit-health, session-handoff or self-assessment."
metadata:
  version: "1"
  updated: "2026-10-12"
  reviewed: "2026-10-12"
user-invocable: true
---

# mission-control — the store as one private page, rebuilt locally, never published

The page is a single self-contained HTML file built from the store's structured sources: the session registry and
its cost ledger, doc frontmatter, the priorities file, the PR-review and sign queues, the latest kit-health walk and
the memory pointers. The engine builds it (`context-db/bin/mission.py`): no model turn, no network request, nothing
leaves the machine. This skill checks that the page is current, rebuilds it when it is not and hands back its path.

## 1. Detect

1. Run `make -C $BATON/context-db mission-diff`. It prints one `mission:` line and changes nothing:
   - `unchanged` (exit 0): the page is current. Skip the rebuild unless the user asked for a fresh one (`FORCE=1`).
   - `changed`, `not built` or `incomplete` (make shows `Error 1`): the page is behind. Go to § 2.
   - `content root not found` (make shows `Error 2`): there is no store to read. Print
     `mission-control: no store at the content root — nothing to build` and stop. This skill never creates one.
2. A store that exists but holds little still builds: every section shows an empty-state line instead of failing.

## 2. Do

1. Run `make -C $BATON/context-db mission-build`. It writes `data.json`, `build.json` and `index.html` under
   `state/mission-control/` in the content root; that directory carries its own `.gitignore`, so the store's git
   never picks the page up. A build with nothing new writes nothing and prints `unchanged`.
   `OUT=<dir>` builds somewhere else and `FORCE=1` rebuilds regardless; pass the same `OUT=` to every target below.
2. A leak hit stops the build (make shows `Error 1`) and writes nothing: stdout lists one
   `<section> · line <n> · <what> · <redacted>` line per hit (a secret shape or an e-mail address in the data).
   Fix the source doc that line points to, then rebuild. Never edit the page or the data files, never loosen the gate,
   and never repeat the redacted value in a reply.
3. Run `make -C $BATON/context-db mission-open`. It prints the absolute path of `index.html` and never starts a
   browser; the user opens that path in a browser on this machine.
4. Run `python3 $BATON/skills/mission-control/check.py <that path>`. Exit 0 means all six sections and both JSON
   blocks are present and a text scan of the markup, the styles and the inline scripts found none of: a remote load,
   a connection or loader call (`fetch(`, `new Worker`, `new Audio` and the like), a markup write (`innerHTML`,
   `outerHTML`, `insertAdjacentHTML`, `document.write`), a navigation (`window.open`, `location.href`, `.assign`,
   `.replace`), a `.src =` assignment, a literal `http(s)://` URL in a script. It reads the page, it does not run it.
   Exit 1 prints one finding per line: a defect in the page shell (the template, a part or the generator's output),
   not in the store. Exit 2 means the check did not run (a usage error or an unreadable page). Report either and do
   not hand the page over as good.
5. Tell the user what the page holds, in one short list:
   - **sessions** — active sessions with a STALE flag, ended sessions with a copy-prompt button, the archived count,
     and for each session its heartbeat, repos, crons and open PRs.
   - **cost** — the spend basis, totals, the last 7 and 30 days, spend by epic, the ledger rows and live sessions.
   - **catalog** — every doc by domain, a search box over path, title, domain, type and tags, and a size warning on
     large docs.
   - **priorities** — the priorities file's checkboxes, read-only, grouped by section.
   - **queues** — the PR-review ledger, the sign-queue jobs and the latest kit-health outcome.
   - **pointers** — memory pointers, the INDEX and SESSION_INDEX generated lines, the sources list and the
     data-as-of line.
6. Say how to read it. A tab opens by its bare hash (`#sessions`, `#cost`, `#catalog`, `#priorities`, `#queues`,
   `#pointers` after the path) and the page remembers the last tab in one browser key; nothing else is stored.
   Staleness and cron times are computed when the page is opened (against the opening time, in the store's display
   zone and in UTC), so the same file reads differently later without a rebuild.
7. Say what the page cannot do. It is read-only: it writes nothing back and runs nothing. It offers copy buttons for
   the commands and prompts you would run yourself (`ctx view <path>` per doc, a `find` per domain and tag, a
   session's registry path, an ended session's saved prompt). Publishing the page anywhere is not part of this skill.
8. A registered session's heartbeat and `session-handoff` already rebuild the page. Rebuild by hand only when § 1
   says the page is behind, or when the user asks for it.

## 3. Report

One line, in the shape that fits the run; the user reads the sections in the page, not in the reply.

- Rebuilt: `mission-control: <path> — data as of <as_of> (rebuilt)`.
- Already current: `mission-control: <path> — data as of <as_of> (already current)`.
- Stopped: `mission-control: stopped — <what>`, then what to fix. Name the shape (a secret shape, an e-mail address,
  a remote reference), never the value.
  - Leak gate (§ 2.2): `<section> · <shape>`, then the source doc that line points to.
  - Check finding (§ 2.4, exit 1): the finding, then the template, part or generator output that produced it, never a
    store doc.
  - Check not run (§ 2.4, exit 2): `check not run · usage error` or `check not run · unreadable page`, whichever it
    was, then run `make -C $BATON/context-db mission-build` first and check again.
- `<as_of>` is the value near the top of the `data.json` beside the page. `null` means no source carried a
  timestamp: write `no source timestamp` instead of a time.

## Related

- `session-register` — creates the registry rows the sessions section reads; its heartbeat rebuilds the page.
- `session-handoff` — ends the session and rebuilds the page so the ended row shows.
- `cost-report` — the spend-versus-work report; the cost section only shows the ledger totals.
- `kit-health` — the audit whose latest outcome the queues section shows.
