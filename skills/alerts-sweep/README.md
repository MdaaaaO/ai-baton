# alerts-sweep

A forked, Sonnet-priced sweep of the Airflow alerts channel every 20 minutes, armed with `/loop 20m /alerts-sweep`.

**Needs:** `systems.airflow` and `systems.slack`; facts `slack.channel airflow-alerts` (the channel it reads),
`airflow.path alerts-state` (where the last-swept timestamp lives) and `airflow.path alerts-kb` (the pattern KB
each message is classified against). All three come from the env store; a missing one makes the worker return
`NEEDS <system>.<kind> <name>` and the main session resolves it with `/env-init`.

**Without it:** on a machine where either flag is false the fork returns one line — `alerts-sweep: not applicable
here — airflow is false` (or `— slack is false`, whichever tripped) — and the loop does nothing else. It never
acts on an alert itself anywhere.

**Example:**

> **user:** `/loop 20m /alerts-sweep`
>
> **claude (fork, every 20 min):** `NO-OP` — or, when something needs the main session: `2 new: <pattern> ×2
> since <timestamp>; state advanced` and the main session decides what to do.
