# Verification plan template — a code change

The default verification plan for an epic that changes code: four gates, and a pipeline block for dbt and DAG changes.
You copy the `## Verification plan` section of this page into the epic issue's body and adapt it. This page also states
the gate format, which every plan and every template under `docs/verification/` uses. Who acts on a gate is
`docs/swarm.md` § Authority matrix; when the epic may close is its § Lifecycle.

**Where it plugs in.** `epic-discover` offers a template when it writes the epic. The verifier re-runs the agent gates
due. The owner signs the human ones. `gates.py status` must exit 0 before the epic closes.

**What breaks if it is ignored.** A change that merged, deployed and broke its first scheduled run counts as done at the
merge. A gate written after the work measures what the work happened to do. A gate with no signer closes on the author's
own word.

## The gate format

A plan is one `## Verification plan` section in the epic issue's body, and one gate is one line:

```
- [ ] VG-<n> <gate> · kind: agent|human · check: <command | test | observation> · evidence: <what counts> · signer: verifier|owner[ · when: systems.<flag>[ | systems.<flag>…]]
```

| Field | Rule |
|---|---|
| `VG-<n>` | The gate's id, unique in the plan and stable for its life. A later gate takes the next number, never a reused one. |
| `<gate>` | What must be true, in one clause. It may hold ` · `. |
| `kind` | `agent` closes on evidence. `human` closes on the owner's decision. A gate with an agent step and then a sign-off is `human`, and the agent's output is what the owner reads; or write it as two gates in a row. |
| `check` | A command when it is exactly one backticked span, else a test or an observation. A `<placeholder>` in a command is filled per child ticket by whoever runs it. |
| `evidence` | What counts as closing the gate, named before the work starts. |
| `signer` | `verifier` on an agent gate, `owner` on a human gate. `gates.py parse` refuses any other. A person who is not the owner reaches a gate through the owner's answer. |
| `when` | Optional, the last field. The capability flags the check needs, any one of them enough, each a real `systems.<flag>`. On a machine where all are false the gate stays open and `gates.py status` says `not applicable here`. Dropping it is the owner's re-plan delta, never an automatic skip. |

**The section.** It ends at the next `## ` heading, so a `### ` heading stays inside it. A prose line is ignored and a
fenced block is skipped. A checkbox or `VG-<n>` bullet that is not a gate is refused, so a typo cannot drop a gate.

**The file.** `gates.py` reads and rewrites a local file and does not touch the network: the orchestrator writes the file
from the issue's body (`gh issue view <n> --json body --jq .body`) and posts it back. The body alone, never its comments,
which are anyone's text: a second `## Verification plan` section is refused.

**Closing a gate.** `gates.py sign` ticks the box and appends a signature, the last thing on the line. A `signed` field and
its record close a gate only together with a ticked box, and `gates.py status` tests the record again, so a box ticked by
hand or a signature that `sign` would refuse leaves the gate open.

| Kind | Call | Appends | Refused unless |
|---|---|---|---|
| `human` | `gates.py sign <file> VG-<n> --by owner --decision "<line>"` | `· signed: owner · decision: <line>` | `--by` is `owner`, and the line is in the grammar of `docs/carousel.md` § After the answer, names this `VG-<n>` in its question and has an answer that starts with `sign`, so one answer closes one gate and a `hold` closes none |
| `agent` | `gates.py sign <file> VG-<n> --by verifier --evidence "<item>"` | `· signed: verifier · proof: <item>` | `--by` is `verifier`, the item is `` `cmd` → `output` ``, a URL, a `#<n>`, a commit or `run <id>`, and one of the gate's `when` flags is on |

No worker signs a gate, and neither does a peer message. The orchestrator asks the owner, writes the decision line to the
epic's ledger and calls `sign` with that line. Signed, a line reads:

```
- [x] VG-7 the tolerance table is agreed · kind: human · check: the owner reads the table · evidence: the owner's decision line · signer: owner · signed: owner · decision: YYYY-MM-DD · VG-7 sign the tolerance table? → sign; not hold — it matches the contract
- [x] VG-8 the suite passes on the merged commit · kind: agent · check: `make test` · evidence: the command's output · signer: verifier · signed: verifier · proof: `make test` → `Ran 12 tests OK`
```

**Reading it.**

| Command | Prints | Exit |
|---|---|---|
| `gates.py parse <file>` | one row per gate | 0 parsed, 1 no gate, 2 malformed or unreadable |
| `gates.py status <file>` | one row per gate, then `GATES: closed` or `GATES: open VG-<n>[, VG-<n>…]` | 0 every gate closed, 3 a gate is open, 1 no gate, 2 malformed or unreadable |

Any exit but 0 from `status` means the epic may not close, so an epic with no plan cannot close either.

**A new template.** One file, `docs/verification/<name>.md`, with this one `## Verification plan` section, ids from `VG-1`,
nothing signed, and every example in a fenced block. `gates.py parse docs/verification/<name>.md` exits 0.

## Verification plan

Each gate holds for every child ticket of the epic. The orchestrator names a gate to the verifier when it falls due, which
is usually at the last child's merge.

- [ ] VG-1 every child's new tests fail on the default branch where there was behaviour to fix · kind: agent · check: in a throw-away checkout, restore the base copy of each changed file and run the child's new tests · evidence: the failing run as `cmd` → `output`, or the PR's statement that the script is new and has no base copy · signer: verifier
- [ ] VG-2 every child merged at a head with an approved review and green CI · kind: agent · check: `gh pr view <pr> --json reviewDecision,headRefOid,statusCheckRollup` · evidence: `reviewDecision: APPROVED` and every required check green, at the head the `READY` line carried · signer: verifier
- [ ] VG-3 every `DW-<n>` of every child is covered and passed · kind: agent · check: `python3 $BATON/context-db/bin/done_when.py coverage --ticket <issue-body> --body <pr-body>` · evidence: `DONE WHEN: covered` per child, and a pass per `DW-<n>` in the task ledger · signer: verifier
- [ ] VG-4 every child's PR body carries a miss check with nothing open and a clean operator brief · kind: agent · check: `python3 $BATON/skills/pr-open/operator_brief.py check --pr <owner/repo> <n>` · evidence: no warning from the command, and a `Miss check: clean` or `Miss check: fixed <n>, open 0` line in the body · signer: verifier

### Pipeline block

Optional. Copy it when the epic changes dbt models or DAGs, and leave it out otherwise. A scheduled run can come days after
the merge, so the orchestrator keeps a watch on `VG-5` and the epic stays open until the run is read. Leave `VG-6` out
where the deploy parses the project with the prod target.

- [ ] VG-5 the first scheduled prod run of the affected pipeline after the deploy is green · kind: agent · check: read that run in the scheduler · evidence: the run's id or URL, with its green state, after the deploy's time · signer: verifier · when: systems.dbt | systems.airflow
- [ ] VG-6 where the deploy parses the project with a non-prod target, the deployed manifest renders the prod relation names · kind: agent · check: read the relation names in the deployed manifest · evidence: `cmd` → `output` that shows a prod relation name from the deployed manifest · signer: verifier · when: systems.dbt
