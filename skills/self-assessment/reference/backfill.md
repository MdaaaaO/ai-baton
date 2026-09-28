# Back-fill mode — compose a week the context DB never saw

Loaded from `SKILL.md`'s Arguments (`--backfill`) and step 3 (zero `.context/` hits). For weeks
before the kit existed, weeks worked from another machine, or any window where step 3's
`.context/` date-grep returns nothing, the systems of record are the **only** source. The week file
is still the charter's fixed format; what changes is provenance and honesty about it.

- **Trigger:** `--backfill` given, or zero `.context/` hits for the window (then say "switching to back-fill
  mode" in the reply and in the method block). A range (`W29..W33`) runs oldest → newest as usual.
- **Sources (each gated by `self_assessment.sources` / `systems.*` exactly as in step 3):**
  - GitHub — the three sweeps (authored / reviewed-by / commenter) **plus** commits in the window
    (`gh search commits --author=$WORKSPACE_GITHUB_LOGIN --author-date=<Mon>..<Sun>`) and issue comments
    (`gh search issues --commenter=…`); PR bodies and review comments are the narrative you would otherwise
    have taken from the context doc — read them, don't just count them.
  - Tracker — the step-3 query **plus** the comments the user authored in the window: tracker query languages
    filter on comment *text*, not comment *author*, so read each hit's comment thread and keep the user's
    comments dated inside the window — the ticket's own closing comment is the measurement of record (step 4
    tie-breaks apply unchanged).
  - Chat (`systems.slack`) — the chat connector's search for the user's **own** messages in the window (one day
    of margin either side, threads included): the ownership rulings, unblocks and decisions no ticket records →
    *Collaboration* and *Learnings* leads only, never *Shipped*.
  - Calendar / meeting titles (`meetings` source; the calendar connector when attached) — attendance is a
    lead for *Collaboration*; an outcome needs a note or a ticket comment to be stated as fact.
  - Warehouse — only to **verify** a number that a ticket or PR claims, never to discover work.
- **Write it honestly.** The method block names the mode, lists which sources were available and which
  were not ("no chat in this environment", "calendar not attached"). *Learnings* opens with
  "reconstructed from systems of record; no contemporaneous notes" — a back-filled week can state what
  shipped and what was said, not what the user felt or intended, so the fourth report section stays to
  one plain sentence unless a 1:1 note or a message in the window supports more.
- **Every claim keeps its evidence link** (PR, ticket, message permalink) in the week file; the report block
  keeps tracker keys and PR links only, as always.
- **Seeding `.context/` is optional and asked once per run**, after the last week of the range: one
  `AskUserQuestion` offering to gap-fill `initiatives/<slug>.md` arcs from the back-filled weeks (yes →
  step 6 for each; no → leave the initiatives untouched, the week files alone are the record). Never seed
  initiative context docs (`TYPE=epic`) from a back-fill — those belong to the sessions that do the work.
- Steps 5–10 run unchanged, with one frontmatter addition: the week file carries **`provenance: back-fill`**
  (that key, not prose, is what tags the ledger card `back-filled <date>`). Then `status: archived` for a
  completed week, index row, drops, `make verify`, the report block, and — where the ledger is on —
  the card.
