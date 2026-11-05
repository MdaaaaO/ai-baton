# commands.md — the engine calls per step

`E=$BATON/context-db/bin`. Each script's own docstring is the full usage; this is the subset a tick runs. `<lane>` is
the orchestrator's registered name, the `from=` on its lines.

| Step | Call |
|---|---|
| 0 | `make -C $BATON/context-db session-register NAME=<lane> EPIC=<key> ROLE=orchestrator [SUCCESSOR_OF=<old>]` |
| 1 | `python3 $E/swarm.py tick <key> --json` · `python3 $E/swarm.py resume <key> --repo-dir <clone> --json` |
| 2 | `python3 $E/swarm_msg.py drain --key <key> <lane>` · `… drain --key <key> <worker> --outbox` · `python3 $E/swarm.py apply <key> "<line>"` · `python3 $E/swarm_msg.py ack --key <key> --as <lane> <id>…` · `python3 $E/swarm_msg.py pending --key <key>` |
| 3 | `python3 $E/swarm.py decision <key> --question "…" --for-ticket <t>` · `… decision <key> --close <id> --line "<decision line>"` |
| 4 | `python3 $E/swarm_budget.py caps <key> <t>` · `python3 $E/swarm.py assign <key> <t> --kind subagent\|peer\|headless --name <worker> --ref <ref> --worktree <dir> --branch <b>` · `python3 $E/swarm_msg.py format ASSIGN <t> <file> --from <lane> --key <key>` · `python3 $E/swarm_msg.py send --key <key> <worker> "<line>" --inbox` |
| 4, incident | `python3 $E/swarm.py add <key> <t> --incident --area <path> --dw DW-<n>` |
| 5 | `python3 $E/swarm.py progress <key> <t> --stall` · `… assign <key> <t> --rebrief` with `format ASSIGN … --rebrief 1 --note "…"` · `… status <key> <t> parked --reason "…"` with `format PARKED <t> --from <lane> --reason "…"` |
| 6 | `python3 $E/swarm_budget.py status <key>` · `python3 $E/swarm_budget.py digest <key>` |
| 7 | `gh pr create --draft --body-file <file>` · `python3 $E/swarm.py status <key> <t> pr --pr <n>` · `… status <key> <t> ready-to-merge --head <sha>` · `gh run watch <id> --exit-status` (background) · `bash $BATON/skills/pr-watch/pr-merge.sh <owner/repo> <pr> <sha>` · `… status <key> <t> merged --pr <n> --sha <sha>` |
| 8 | `python3 $E/gates.py status <plan>` · `python3 $E/gates.py sign <plan> VG-<n> --by owner --decision "<line>"` · `… status <key> <t> blocked --reason "…"` · `… status <key> <t> done` |
| 10 | `python3 $E/swarm.py render <key>` |

A fix request carries its findings inline, never as a file path alone: a scratch file can be lost in a restart.
`swarm.py assign` refuses past the caps, before the plan's go, under a `STOP` or `HOLD` and with a dependency not done;
a refusal is the answer, never a reason to retry around it.
