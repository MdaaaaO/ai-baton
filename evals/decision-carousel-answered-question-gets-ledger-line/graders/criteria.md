---
type: llm
weight: 1
---

Per `docs/carousel.md` § After the answer, an answered carousel question gets one ledger line in the
context doc's *Key decisions & gotchas* section, in the grammar `YYYY-MM-DD · <question> → <chosen>;
not <rejected options> — <one clause why>`. The response acts on the answer (opens, or describes
opening, the re-run ticket rather than continuing to retry) and also writes, or clearly describes
writing, exactly one such line for this decision — naming the chosen option (open the ticket now)
and the rejected one (keep retrying), with a reason, and a date. It writes that line through the
store's write tools (`ctx_log` / `ctx_insert`, or the `ctx_adapter.py ctx …` Bash fallback) into
`.context/widgets/482-lint-flake.md`, never by editing that file directly with a generic file-write
tool. A response that only acts on the decision without ever mentioning or producing the ledger line
does not meet this bar.
