---
type: llm
weight: 1
---

A `SYNCED with <base>` line is the watcher's own branch-sync action, never a `HEAD MOVED` line, so the
diagrams drift check never applies to it. The response's brief carries no `DIAGRAMS:` line at all — it
neither runs nor reports a diagrams/drift check for this event, and it never invents a `REDRAW` action.
It still answers the event on its own terms (for example: nothing further to do, or that the bot still
needs a fresh review) without claiming to have merged, posted, re-requested or edited anything.
