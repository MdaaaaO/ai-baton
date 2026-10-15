# Metrics — what each one is and what it is not

Read on demand from `SKILL.md` when reading or explaining a report.

| metric | definition | reads as |
|---|---|---|
| **in/out** | (input + cache-write + cache-read) ÷ output tokens, weekdays only | prefix dragged per output token — the **habit** number, lower is better |
| **fixed $/Mout** | every token repriced at ONE list price (`cost.prices.default`, else `session_stats.DEFAULT_PRICES`, the top model's) ÷ Mout | habit in $, free of routing |
| **$/Mout** | billed (or estimated) $ ÷ million output tokens | habit × routing |
| **cheap%** | Sonnet/Haiku share of weekday output tokens; `–` when any row has no model (`unknown`, CSV without a model column) | the **routing** signal — unknown is not 0 % cheap |
| **haiku%** | Haiku alone, same weekday denominator and `–` rule as cheap% | a Sonnet→Haiku switch moves this, never cheap% |
| **$/wday, Mout/wday** | weekday-only $ and output ÷ weekdays (ISO 1–5) | rate; every ratio above is weekday-only too, so the decomposition is an exact identity; weekend rows count only in the $ total |
| **$/PR, $/ticket** | $ ÷ PRs opened / tickets closed in the window | noisy under ~10 units — read per phase, never per week |
| **decomposition** | $/wday change = volume × habit × routing, log-additive shares | which lever moved; `avoided $/wday` = after-output at before-$/Mout minus actual |
| **control** (org only) | every other user in the table, weekdays, per user-day, aggregate | did the market move too? never names, never ranks |

The ratio columns (in/out, $/Mout, cheap%, haiku%) are weekday-only, so they differ slightly from a report that
computes them over all days; the fixture smoke run below is the reference a reader can check.

## Fixture smoke run

`fixtures/` holds `daily.sample.json`, `control.sample.json`, `prs.sample.tsv`, `tickets.sample.tsv` (synthetic,
three weeks, two models). `cost_report.py` imports `kit_profile` and `session_stats` from `context-db/bin`.

```
python3 cost_report.py report --daily fixtures/daily.sample.json --control fixtures/control.sample.json --prs fixtures/prs.sample.tsv --tickets fixtures/tickets.sample.tsv --phase before=2026-01-05..2026-01-16 --phase after=2026-01-19..2026-01-23
```
