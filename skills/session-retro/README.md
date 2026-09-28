# session-retro

A forked, Sonnet-priced self-check a session runs at wind-down: did it follow the kit, where did the user have to
correct it, and which of those point at a gap in the kit rather than a slip of this one session.

**Needs:** nothing beyond the kit. The transcript comes from `$CLAUDE_CODE_SESSION_ID` (or a session id / transcript
path as the argument). `context-db/bin/session_retro.py` extracts the candidates without a model call: user messages
with the turn before them, denials, failed commands, and the rule checks a script can decide.

**Without a transcript:** the fork returns `session-retro: no transcript found — nothing to check` and stops.

**What happens next** is the main session's: per kit gap it offers an issue on the kit repo (`ticket-open`
conventions, paraphrased, no transcript quotes or workspace paths); per session slip it offers a feedback memory.
Nothing is created without the user's yes.

**Example:**

> **user:** `/session-retro`
>
> **claude (fork):** `miss · pr-open § 3 labels · t4 09-27 20:40 · session slip — PR opened without labels, none added later`
> and `NEXT (main session): …`. The main session then asks: "One session slip, no kit gaps. Save a feedback memory?"
