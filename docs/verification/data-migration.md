# Verification plan template — a data migration

The verification plan for an epic that moves, copies, backfills or re-models stored data: twenty gates from the mapping
to the decommission. You copy the `## Verification plan` section of this page into the epic issue's body and adapt it.
The gate format is `docs/verification/code-change.md` § The gate format, and this page adds no field to it. Who acts on
a gate is `docs/swarm.md` § Authority matrix; when the epic may close is its § Lifecycle.

**Where it plugs in.** `epic-discover` recommends this template for an epic that moves, copies, backfills or transforms
stored data. A migration's children merge code too, so the plan also takes the code-change template's four gates, as
`VG-21` to `VG-24` after these twenty, and a strategy's gate (§ Strategies) takes the next free id, `VG-25`. Where the
epic changes dbt models or DAGs, code-change's optional pipeline block comes too, after the strategy's gate: its `VG-5`
as `VG-26` and its `VG-6` as `VG-27`, so no copied gate collides with `VG-25`. The ids on this page stay as written, and
so do the gates that cite each other. The verifier signs the agent gates in its gate runs and the owner signs the human
ones. `gates.py status` must exit 0 before the epic closes.

**What breaks if it is ignored.** A migration counts as done when the new tables fill, and the differences surface weeks
later: a time zone shift, a null read as zero, a future timestamp, a key whose grain changed. A tolerance chosen after the
first run is fitted to that run. A rollback nobody rehearsed takes longer than its window. An old path dropped on a
date, not on evidence, cannot be restored.

## Who signs

Every `human` gate here says `signer: owner`, and `gates.py parse` refuses any other. Where a gate needs a table's, a
metric's or a dashboard's owner, that person reaches the gate through the owner's answer: the orchestrator asks the
owner, the owner's decision line names who approved, and `gates.py sign --by owner` records it. A gate with an agent
step and then a sign-off is `kind: human`, and the agent's output is what the owner reads: `VG-12` and `VG-16` are two.

An agent gate that runs a tool against the data names the flags it needs in `when:`. On a machine where they are all
false the gate stays open as `not applicable here`, and the verifier fails it on that one line instead of improvising a
substitute. The kit names the shape of each check; the environment names the tool that runs it.

## Tolerances

A tolerance is a threshold or an accepted difference that a gate reads. The epic keeps its tolerances in its body, as a
`### Tolerances` table under `## Verification plan`, one row per table and check:

| Column | Holds |
|---|---|
| Table | the target table |
| Check | the layer (§ The layered order) and the measure: a count, a hash total, a column's null rate, a metric |
| Tolerance | the bound, absolute or relative; `0` where the check must match exactly |
| Window | the partitions it applies to |
| Decision | the owner's decision line that set it |

- **Written before the first run.** `VG-4` is signed before the first reconciliation run starts, so a bound is never
  fitted to a result. Every proof that reads a tolerance names a run that started after that signature.
- **Only a human decision may loosen a tolerance**, or change one at all. The change is the owner's decision line,
  ledgered in the epic context doc's *Key decisions & gotchas* (`docs/carousel.md` § After the answer) and mirrored in
  the task ledger's `decisions` (`swarm.py decision`); the table's row and its decision line change together. No agent
  loosens a tolerance. An agent that finds a check outside its bound records the miss and stops that layer. It never
  widens the bound, narrows the window, filters rows out or moves the difference to the register to make it pass.
- **A changed bound applies from the next run on.** A run under the old bound is no proof for a gate still open.
- **The accepted-differences register.** A difference the key-level diff finds that is correct, such as an old bug the
  new path fixes or a deliberate change of rounding, goes in the epic body's `### Accepted differences` table: one row
  per class of difference, never per key, with the table, the rows it covers as a predicate, the columns, why, the
  count at the last run and the owner's decision line. An accepted difference is a tolerance too, so a row with no
  decision line explains nothing.

## The reconciliation report

One report per run, written by the run itself where the environment keeps it. An agent gate's proof points at the
runs, never a hand copy of their numbers: exactly `run <id>` where one run's report holds the proof, else a command that
reads the reports, as `cmd` → `output`.

| Field | Holds |
|---|---|
| `run_id` | unique per run; the proof `run <id>` names it |
| `commit` | the commit of the new path's code that the run read |
| `watermark` | the latest load time both paths include; a row loaded after it is outside the run |
| `window` | the first and last partition compared |
| `table` | the target table, and the source tables it is compared with |
| `layer` | one row of § The layered order |
| `check` | the measure: a count, a hash total, a column's null rate, a key diff, a metric |
| `old` / `new` | the value on each path |
| `difference` | absolute and relative |
| `tolerance` | the bound in force, with the decision line behind it |
| `explained` | the register rows that account for the difference |
| `result` | `pass`, `fail`, or `skipped` when a layer above it failed |
| `started` / `finished` | the run's times, in UTC |
| `by` | the identity the run ran under, whose access `VG-15` reads |

## The layered order

Cheapest first, each a gate for the next: a layer runs only where every layer above it passed for that table and
window, and a failed layer marks the rest `skipped`. A count that does not match turns every hash, key diff and metric
below it into noise. Both paths normalize a row the same way before it is hashed or compared: one null token, one time
zone, one decimal scale, trimmed text.

| # | Layer | Compares, old path against new | Catches | Read by |
|---|---|---|---|---|
| 1 | Schema | column names, types, nullability, keys | a dropped or renamed column, a narrowed type | `VG-2`, `VG-3` |
| 2 | Counts | rows per table and partition | a missing or doubled load | `VG-6` |
| 3 | Hashes | an order-free hash total of each partition's normalized rows | a changed value under an unchanged count | `VG-6` |
| 4 | Column statistics | null rate, distinct count, minimum and maximum per column | a silent default, a truncation, a null read as zero | `VG-6` |
| 5 | Distributions | quantiles of each measure, frequencies of each category, rows per hour | a unit change, a time zone shift, skew | `VG-6`, `VG-11` |
| 6 | Keys | a full outer join on the business key, column by column | which rows differ, and how | `VG-7`, `VG-8`, `VG-9` |
| 7 | Metrics | each business metric, computed on each path | a definition that drifted | `VG-11`, `VG-12` |
| 8 | Integrity | foreign keys resolve, declared keys are unique | an orphan, a duplicate | `VG-10` |
| 9 | Late data | layers 2 to 8 again on the last window, once its late rows have landed | a row that arrived after the watermark | `VG-13` |

## Edge cases

The golden dataset of `VG-5` holds at least one row of each, with the output expected of it written before the run:
a timestamp on each side of a daylight-saving change; a null, an empty string and a zero in the same column; a future
and a far-past timestamp; two rows on one business key; a late and a re-delivered row; a deleted and a soft-deleted
row; non-ASCII text at the column's maximum length; a negative amount and one that rounds at the decimal scale; and,
where the grain changes, one old row that becomes many new ones.

## Strategies

The cutover strategy is the owner's call in the epic interview. Each adds one gate to the twenty, written after
code-change's four with the next free id, `VG-25`, and due in the step its row names (§ When each gate falls due).

| Strategy | What it is | The gate it adds | Due in |
|---|---|---|---|
| Big-bang | everything moves in one cutover, with no parallel period | the whole cutover rehearsed on a production-size copy, done within the freeze window's length | Prove equivalence |
| Strangler | consumers move one at a time behind a stable name | per consumer moved, its reads reach the new path only, and the old path's reads for it are zero | After the cutover |
| Parallel run | both paths run on the same input for an agreed period | `<n>` consecutive daily reconciliations within tolerance, with `VG-11`'s business cycle inside them | Prove equivalence |
| Dual-write | the producer writes both stores | every write on one path is on the other within `<lag>`, and a failed write on either side is counted, never retried silently | Prove equivalence |
| Shadow | the new path runs on a copy of the input and no consumer reads it | no consumer reads the shadow output and it writes no shared target, read from the access log | Prove equivalence |
| Blue-green | two complete environments, and a switch moves the readers | the switch rehearsed both ways on the two environments: the readers reach the new one, and switching back restores the old reads within the rollback window | Prove equivalence |

The strangler's gate is the one that cannot come first: it reads consumers already moved, so it falls due after the
cutover, and `VG-17` signs a strangler's cutover with `VG-16`'s rollback rehearsal as its only strategy proof.

## When each gate falls due

The gates are in the order a migration meets them, and this plan orders them in steps: one gate run per step, not one
for the epic. Each step's run reads only the gates due in that step (the verifier's `GATES:` line), after the children
the step names under *Merged first* have merged and recorded their passes (its `CHILDREN:` line), and the next step
starts only once that step's gates are closed. Every gate of the plan is due in one step: these twenty, the strategy's
gate, code-change's four and, where the epic copied it, the pipeline block.

The step names a child by its kind, and the orchestrator puts that kind's tickets in the run's `CHILDREN:` line. A
migration has three: the **build children**, which write the new path (its tables, transforms and backfills); the
**cutover child**, which moves the readers, one per consumer under a strangler; and the **decommission child**, which
drops the old path.

| Step | Gates | Merged first | Closed before |
|---|---|---|---|
| Before reconciliation | `VG-1` to `VG-5` | every build child | the first reconciliation run; `VG-1`, the owner's, before any build child starts a target table |
| Prove equivalence | `VG-6` to `VG-16`, and the strategy's gate `VG-25` where § Strategies names this step | every build child | the go/no-go, `VG-17` |
| Cut over | `VG-17` | every build child, and no cutover child | the cutover child starts |
| After the cutover | `VG-18` to `VG-20`, and the strategy's gate `VG-25` where § Strategies names this step | every build child and every cutover child | the decommission child starts; `VG-20` last, after the others of this step |
| Every child merged | `VG-21` to `VG-24`, the code-change template's four, and `VG-26` and `VG-27` where the epic copied the pipeline block | every child, the decommission child too | the epic closes |

`VG-17` reads every gate due before the cutover, so the cutover is never signed without the strategy's proof where that
proof can come first. The gates after the cutover fall due days or weeks after the last cutover child merges, so the
orchestrator keeps a watch on each (`swarm.py watch`), names it in a later gate run, and the epic stays open until that
run reads it.

## Verification plan

Each gate holds for the epic's whole table set and takes one signature. Its check runs once over every target table of
the set. An agent gate's proof is one item `gates.py sign` takes: exactly `run <id>` where one run's report covers
every table of the set, its `table` field naming them, else one `cmd` → `output` line whose output names the tables and
the runs it read. A `<placeholder>` is filled in the epic: `<cycle>` the business cycle, `<dates>` the as-of dates,
`<late>` the late-arrival window, `<window>` the rollback window, `<cycles>` the clean cycles, `<days>` the quiet days.
A gate stands alone: the verifier signs those that hold, and the next run names only the ones still open.

- [ ] VG-1 every target table has a mapping document approved by the table's owner · kind: human · check: the owner reads each target table's mapping (source columns, transform, target column, grain, business key, and the columns dropped and why) with the table owner's approval of it · evidence: the owner's decision line, naming each target table and who approved its mapping · signer: owner
- [ ] VG-2 the lineage generated from the code matches the mapping, with no unmapped column · kind: agent · check: generate each target table's column lineage from the merged code, never by hand, and diff it with the mapping · evidence: `cmd` → `output` with zero lineage edges the mapping lacks, zero target columns with no source, and zero source columns neither mapped nor listed as dropped · signer: verifier · when: systems.dbt | systems.datalake
- [ ] VG-3 every consumer's schema and contract tests pass against the new path · kind: agent · check: run each consumer's contract (column names, types, nullability, keys, accepted values) against the new tables · evidence: `run <id>` where one run tested every consumer's contract green, else `cmd` → `output` naming each consumer and its green run · signer: verifier · when: systems.dbt | systems.datalake
- [ ] VG-4 the reconciliation tolerances are written per table and check and approved before the first reconciliation run, and only a human decision may loosen a tolerance, or change one at all, after that · kind: human · check: the owner reads the `### Tolerances` table (§ Tolerances); a later change is the owner's decision line, ledgered in the epic context doc's *Key decisions & gotchas* and mirrored in the task ledger's `decisions` · evidence: the owner's decision line, dated before the first reconciliation run started · signer: owner
- [ ] VG-5 a golden dataset with the edge cases passes on the new path · kind: agent · check: run the new path on a fixed golden input that holds every case of § Edge cases, and compare it with the expected output written before the run · evidence: `run <id>` with zero differences from the expected output · signer: verifier · when: systems.dbt | systems.datalake
- [ ] VG-6 every target table reconciles over the window, its counts, hash totals, column statistics and distributions within tolerance · kind: agent · check: read layers 2 to 5 of the reconciliation report of a run that started after `VG-4` was signed · evidence: `run <id>` whose report has every row `pass` for every target table, with its commit and watermark · signer: verifier · when: systems.datalake
- [ ] VG-7 the key-level diff leaves zero unexplained differences · kind: agent · check: read layer 6 of the same run, a full outer join of both paths on the business key, with every differing key matched to a row of the `### Accepted differences` register · evidence: `run <id>` with zero unexplained keys per target table, and every register row it used carrying the owner's decision line · signer: verifier · when: systems.datalake
- [ ] VG-8 every table whose grain changed is proven by a roll-up to the old grain and by business-key equality both ways · kind: agent · check: roll the new table up to the old grain and compare the measures; then every old business key is in the new table and every new one in the old, at the old grain · evidence: `run <id>` with the roll-up within tolerance and zero keys missing in either direction, per grain-changed table · signer: verifier · when: systems.datalake
- [ ] VG-9 every slowly changing dimension answers the same at each point in time · kind: agent · check: at each of `<dates>`, the version valid on each path is the same per key; the validity ranges have no gap and no overlap; each key has one current row · evidence: `run <id>` with zero mismatches, gaps, overlaps and doubled current rows per dimension · signer: verifier · when: systems.datalake
- [ ] VG-10 referential integrity and uniqueness hold on the new path · kind: agent · check: every foreign key resolves to its parent, and every declared unique or business key is unique · evidence: zero orphans and zero duplicates per target table, as `run <id>` or `cmd` → `output` · signer: verifier · when: systems.dbt | systems.datalake
- [ ] VG-11 every business metric is signed by its owner over one full business cycle · kind: human · check: the owner reads the agent's comparison of each metric on both paths over `<cycle>` (layer 7), with each metric owner's sign-off · evidence: the owner's decision line, naming the cycle, the metrics and who signed each · signer: owner
- [ ] VG-12 the top dashboards and queries are re-pointed to the new path and signed · kind: human · check: the agent re-points each dashboard and saved query on the epic's list and runs it on both paths; the owner reads the results side by side, with each dashboard owner's sign-off · evidence: the owner's decision line, naming the list and who signed each · signer: owner
- [ ] VG-13 late-arriving rows are handled, with the watermark recorded · kind: agent · check: once `<late>` has passed, run layers 2 to 8 again on the last window (layer 9) and compare both runs' watermarks · evidence: `cmd` → `output` reading both runs' reports on the same window, before and after the late rows landed: both run ids, both within tolerance, each with its watermark · signer: verifier · when: systems.datalake
- [ ] VG-14 every schema change and every run on the new path has an audit entry · kind: agent · check: match the schema changes the children merged and every pipeline run since the first against the audit log · evidence: zero schema changes and zero runs without an entry, as `cmd` → `output` · signer: verifier · when: systems.datalake | systems.airflow
- [ ] VG-15 the verifier is independent of the author by access · kind: human · check: the owner reads the access the verifier's runs held: read-only on both paths, no write on the new one, and no child authored · evidence: the owner's decision line, naming the identity the verifier ran under and the access it held from the first reconciliation run on · signer: owner
- [ ] VG-16 the rollback is rehearsed within its window · kind: human · check: the agent rehearses the rollback (the trigger, the readers moved back to the old path, the reconciliation after it) on a non-production copy and times it; the owner reads the time against `<window>` · evidence: the owner's decision line, naming the rehearsal's `run <id>` and how long it took · signer: owner
- [ ] VG-17 the go/no-go is signed and the freeze announced · kind: human · check: the owner reads `gates.py status` with every gate due before the cutover closed (§ When each gate falls due: `VG-1` to `VG-16`, and the strategy's gate where § Strategies names Prove equivalence), the cutover time, the freeze window, and the rollback trigger with its bound · evidence: the owner's decision line, naming the cutover time, the freeze window, the trigger and where the freeze was announced · signer: owner
- [ ] VG-18 the post-cutover monitors run clean, under a trigger that rolls back first and informs after · kind: agent · check: read every monitor run since the cutover against the trigger `VG-17` agreed, and the trigger's set-up: a breach moves the readers back without waiting for a person, then sends the notice · evidence: `cmd` → `output` reading every monitor run since the cutover: `<cycles>` clean cycles, zero breaches, the first and the last run named; a trigger that fired leaves this gate open, with its rollback run timed before its notice · signer: verifier · when: systems.datalake | systems.airflow
- [ ] VG-19 the old path has zero reads for the agreed days · kind: agent · check: read the old path's access log for `<days>` consecutive days after the cutover, every reader counted, jobs and people alike · evidence: `cmd` → `output` with zero reads on every day of the span, naming its first and last day · signer: verifier · when: systems.datalake
- [ ] VG-20 the decommission is approved after the clean cycles and a verified archive · kind: human · check: the owner reads `VG-18`'s clean cycles, `VG-19`'s zero-read span, the strategy's gate where § Strategies names After the cutover, and the agent's restore test of the archive (layers 2 and 3 of the restored copy against the old path) · evidence: the owner's decision line, naming where the archive is, its restore `run <id>` and the decommission date · signer: owner

## Worked example

A fictitious table set, every value a placeholder: `<old>.orders`, one row per order, moves to `<new>.order_line`, one
row per order line, so its grain changes; `<old>.customers` becomes `<new>.customer_dim`, a dimension that keeps its
history. The owner chose a parallel run. The epic's section, with the gates the table set adapts:

````
## Verification plan

### Tolerances

| Table | Check | Tolerance | Window | Decision |
|---|---|---|---|---|
| `<new>.order_line` | count, rolled up to orders | 0 | `<first>` to `<last>` | `<decision line>` |
| `<new>.order_line` | hash total of `amount`, rolled up to orders | 0 | `<first>` to `<last>` | `<decision line>` |
| `<new>.order_line` | null rate of `discount` | `<pct>` | `<first>` to `<last>` | `<decision line>` |
| `<new>.customer_dim` | count of current rows | 0 | `<first>` to `<last>` | `<decision line>` |
| `<new>.order_line` | metric `<revenue>` per day | `<abs>` | `<first>` to `<last>` | `<decision line>` |

### Accepted differences

| Table | Rows | Columns | Why | Count | Decision |
|---|---|---|---|---|---|
| `<new>.order_line` | `<predicate>` | `amount` | the old path rounded per order, the new one rounds per line | `<n>` | `<decision line>` |

- [ ] VG-1 every target table has a mapping document approved by the table's owner · kind: human · check: the owner reads the mappings of `<new>.order_line` and `<new>.customer_dim`, each with its table owner's approval · evidence: the owner's decision line, naming both tables and who approved each mapping · signer: owner
- [ ] VG-8 every table whose grain changed is proven by a roll-up to the old grain and by business-key equality both ways · kind: agent · check: roll `<new>.order_line` up to one row per order and compare `amount` and the line count with `<old>.orders`; then every order id is on both paths · evidence: `run <id>` with the roll-up within tolerance and zero order ids missing in either direction · signer: verifier · when: systems.datalake
- [ ] VG-9 every slowly changing dimension answers the same at each point in time · kind: agent · check: at each of `<dates>`, the version of each customer valid in `<new>.customer_dim` matches `<old>.customers`; no gap or overlap in its validity ranges; one current row per customer · evidence: `run <id>` with zero mismatches, gaps, overlaps and doubled current rows · signer: verifier · when: systems.datalake
- [ ] VG-11 every business metric is signed by its owner over one full business cycle · kind: human · check: the owner reads the agent's comparison of `<revenue>` and `<order count>` on both paths over `<cycle>`, with each metric owner's sign-off · evidence: the owner's decision line, naming the cycle, both metrics and who signed each · signer: owner
- [ ] VG-25 the parallel run holds `<n>` consecutive daily reconciliations within tolerance, with the business cycle of VG-11 inside them · kind: agent · check: read the reports of the last `<n>` daily runs · evidence: `cmd` → `output` reading the last `<n>` daily runs' reports, each with every row `pass`, the first and the last run named · signer: verifier · when: systems.datalake

The other gates stay as the template writes them, and the code-change template's four follow as VG-21 to VG-24.
````
