#!/usr/bin/env python3
"""cost_report.py — the user's own Claude Code spend against the work shipped.

Two modes, decided from the env config (`kit_profile.py get cost` + `systems.datalake`):

  org      `cost.spend_table` set and `systems.datalake` true → BILLED cost from the warehouse
           spend table (`sql` prints the two queries; `ingest` normalises the query results) plus an
           ANONYMOUS control (every other user in the table, aggregate per user-day, never named).
  private  otherwise → ESTIMATED list-price cost from the local Claude Code transcripts
           (`collect-private`: ~/.claude/projects/*/*.jsonl + subagents, priced per model like
           session_stats.py), optionally merged with a CSV export (`--csv`). No control.

Work units are PRs opened/merged (`work-prs`, GitHub search by the user's login) and tickets closed
(`work-tickets` for a GitHub tracker; for any other tracker the skill runs the env store's
`tracker.query resolved_by_me` through the tracker tool and saves a TSV — see SKILL.md). Every report opens with a **Basis** block: billed vs estimated, price
table, which work sources were reachable, control present or not, the rules applied.

Subcommands
  mode                                  print the resolved mode + basis as JSON
  sql [--since D] [--until D]           org mode: print the own-rows daily query and the control query
  ingest --rows F [--control-rows F] [--out daily.json] [--control-out control.json]
                                        normalise query results (JSON array of row objects, any case)
  collect-private [--since D] [--until D] [--csv F] [--out daily.json]
  work-prs [--since D] [--until D] [--out prs.tsv]
  work-tickets [--since D] [--until D] [--out tickets.tsv]   (tracker.kind == github only)
  report --daily daily.json [--control control.json] [--prs prs.tsv] [--tickets tickets.tsv]
         [--phase NAME=START..END]... [--view rolling7|weekly|phases|all] [--out report.md] [--json out.json]

Rules baked in: per-day rates use WEEKDAYS only (ISO 1–5);
work data is CAPPED at the last spend day; a phase list may name an excluded changeover day
(`X=YYYY-MM-DD..YYYY-MM-DD`, a one-day phase); the fixed-price basis reprices every token at ONE list price so the
habit metric (fixed $/Mout) is free of model routing; lines changed are never a denominator.
Stdlib only.
"""
from __future__ import annotations
import argparse
import csv
import glob
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent.parent
sys.path.insert(0, str(KIT / "context-db" / "bin"))
import kit_profile as profile  # noqa: E402
import session_stats  # noqa: E402  (price_for, DEFAULT_PRICES)
import transcripts  # noqa: E402  (the shared transcript reader)

FIXED = session_stats.DEFAULT_PRICES  # (in, cache_write, cache_read, out) $/Mtok — the habit basis
CHEAP = ("sonnet", "haiku")
COLUMN_KEYS = ("date", "email", "model", "cost", "input", "output", "cache_read", "cache_write")
# Org mode has NO default column names or row filter: every warehouse has its own schema, and a default
# would silently build SQL for one environment's table. cost.columns + cost.filters are required there.
CSV_ALIASES = {  # private-mode CSV header → canonical field (lower-cased, non-alnum stripped)
    "date": "d", "day": "d", "spenddate": "d", "usagedate": "d", "timestamp": "d",
    "model": "model", "modelid": "model", "modelname": "model",
    "costusd": "usd", "cost": "usd", "usd": "usd", "amount": "usd",
    "inputtokens": "inp", "input": "inp", "uncachedinputtokens": "inp",
    "outputtokens": "outp", "output": "outp",
    "cachedinputtokens": "cached", "cachereadtokens": "cached", "cachereadinputtokens": "cached",
    "cachecreationtokens": "cwrite", "cachewritetokens": "cwrite", "cachecreationinputtokens": "cwrite",
}


# ----------------------------------------------------------------------------- config / mode
def cfg() -> dict:
    c = profile.get("cost", {}) or {}
    return c if isinstance(c, dict) else {}


def columns() -> dict:
    c = cfg().get("columns") or {}
    return {k: str(c.get(k) or "").strip() for k in COLUMN_KEYS}


def user_email() -> str:
    return str(cfg().get("email") or "").strip()


def missing_org_config() -> list[str]:
    """Every cost.* value org mode needs and does not have — the caller exits 2 with the list."""
    c = cfg()
    miss = [f"cost.columns.{k}" for k, v in columns().items() if not v]
    if "filters" not in c:
        miss.append("cost.filters (the predicate selecting Claude Code rows; \"\" for none)")
    if not user_email():
        miss.append("cost.email")
    return miss


def resolve_mode() -> dict:
    c = cfg()
    datalake = bool(profile.get("systems.datalake", False))
    table = str(c.get("spend_table") or "").strip()
    org = datalake and bool(table)
    return {
        "mode": "org" if org else "private",
        "cost_basis": "billed" if org else "estimated (list price per model, session_stats.py table)",
        "spend_table": table if org else None,
        "email": user_email() if org else None,
        "filters": (c.get("filters") if "filters" in c else None) if org else None,
        "missing": missing_org_config() if org else [],
        "control": "anonymous aggregate of every other user in the spend table (weekdays, per user-day)" if org else None,
        "tracker": profile.get("tracker.kind", "none"),
        "github_login": profile.identity("WORKSPACE_GITHUB_LOGIN"),  # plugin option, else settings.local.json env
        "github_org": profile.get("github.org", ""),
        "extra_repos": c.get("extra_repos", []),
        "phases": c.get("phases", []),
        "fixed_prices": {"input": FIXED[0], "cache_write": FIXED[1], "cache_read": FIXED[2], "output": FIXED[3]},
        "why": ("systems.datalake true and cost.spend_table set" if org else
                "no cost.spend_table (or systems.datalake false) → local transcripts"),
    }


# ----------------------------------------------------------------------------- org: column mapping
# What a `DESCRIBE TABLE` column is usually called per role. Exact match (case-insensitive, `_` = ` `) scores
# 1.0; a column whose tokens contain every token of a vocabulary entry scores 0.8; else difflib ratio. A role
# is `ok` when exactly one column matches exactly, else when exactly one scores 0.8 — anything else is a `?`
# the user settles (the manifest's verify clause: "propose by name similarity and confirm the ambiguous ones").
# `cache_creation_input_tokens` contains the tokens of `input_tokens`, so an exact `input_tokens` must win.
COLUMN_VOCAB = {
    "date": ["date", "day", "usage_date", "spend_date", "activity_date", "dt", "event_date"],
    "email": ["email", "user_email", "user", "actor_email", "member_email"],
    "model": ["model", "model_name", "model_id"],
    "cost": ["cost", "cost_usd", "spend", "spend_usd", "amount", "billed_usd", "usd"],
    "input": ["input_tokens", "input", "prompt_tokens", "uncached_input_tokens", "tokens_in", "in_tokens"],
    "output": ["output_tokens", "output", "completion_tokens", "tokens_out", "out_tokens"],
    "cache_read": ["cache_read_tokens", "cache_read_input_tokens", "cache_read", "cached_tokens", "cached_input_tokens"],
    "cache_write": ["cache_write_tokens", "cache_creation_tokens", "cache_creation_input_tokens", "cache_write"],
}


def _describe_names(rows: list[dict]) -> list[str]:
    """Column names out of a DESCRIBE/INFORMATION_SCHEMA result of any vendor: the first key that looks like one."""
    out: list[str] = []
    for r in _lower_keys(rows):
        for k in ("name", "column_name", "column", "field", "col_name"):
            if r.get(k):
                out.append(str(r[k])); break
    return out


def propose_columns(names: list[str]) -> dict[str, dict]:
    import difflib
    def toks(s: str) -> set[str]:
        return set(re.split(r"[^a-z0-9]+", s.lower())) - {""}
    prop: dict[str, dict] = {}
    for role, vocab in COLUMN_VOCAB.items():
        scored = []
        for n in names:
            nl, nt = n.lower(), toks(n)
            best = 0.0
            for v in vocab:
                if nl == v or nl.replace(" ", "_") == v:
                    best = max(best, 1.0)
                elif toks(v) <= nt:
                    best = max(best, 0.8)
                else:
                    best = max(best, difflib.SequenceMatcher(None, nl, v).ratio() * 0.7)
            scored.append((round(best, 2), n))
        scored.sort(key=lambda x: (-x[0], x[1]))
        exact = [n for s, n in scored if s >= 1.0]
        strong = exact if len(exact) == 1 else ([n for s, n in scored if s >= 0.8] if not exact else exact)
        prop[role] = {"column": strong[0] if len(strong) == 1 else "", "score": "ok" if len(strong) == 1 else "?",
                      "alternatives": [f"{n} ({s})" for s, n in scored[:3] if s > 0]}
    # One column serves one role. Two roles that picked the same column (no plain input column, so `input` fell
    # back to `cache_read_input_tokens`, which `cache_read` also owns) are both demoted to `?` — the conflict
    # becomes the user's one question instead of billed input tokens silently being the cache-read tokens.
    owners: dict[str, list[str]] = {}
    for role, p in prop.items():
        if p["column"]:
            owners.setdefault(p["column"], []).append(role)
    for col, roles in owners.items():
        if len(roles) > 1:
            for role in roles:
                prop[role]["column"], prop[role]["score"] = "", "?"
                prop[role]["alternatives"].insert(0, f"{col} also claimed by {', '.join(r for r in roles if r != role)}")
    return prop


def cmd_propose_columns(a) -> int:
    rows = json.load(open(a.describe))
    rows = rows if isinstance(rows, list) else rows.get("rows", [])
    if not all(isinstance(r, dict) for r in rows):
        print("DESCRIBE result rows are not objects (a connector that returns arrays) — re-save as row objects with a name/column_name field", file=sys.stderr)
        return 2
    names = _describe_names(rows)
    if not names:
        print("no column names in the DESCRIBE result (expected row objects with a name/column_name field)", file=sys.stderr)
        return 2
    prop = propose_columns(names)
    print("| role | column | score | alternatives |\n|---|---|---|---|")
    for role, p in prop.items():
        print(f"| {role} | {p['column'] or '<?>'} | {p['score']} | {', '.join(p['alternatives']) or '–'} |")
    mapping = {role: (p["column"] or "<?>") for role, p in prop.items()}
    open_ = [r for r, p in prop.items() if not p["column"]]
    print("\nkb.py config-set cost.columns '" + json.dumps(mapping) + "'")
    sys.stdout.flush()
    if open_:
        print(f"\n{len(open_)} role(s) ambiguous ({', '.join(open_)}) — confirm with the user in one question, fill the <?> slots, then run the line",
              file=sys.stderr)
        return 3
    print("\nevery role maps to exactly one column — run the line as printed", file=sys.stderr)
    return 0


# ----------------------------------------------------------------------------- org: SQL + ingest
def cmd_sql(a) -> int:
    m = resolve_mode()
    if m["mode"] != "org":
        print("not org mode: set cost.spend_table (kb.py config-set cost '{...}') and systems.datalake true", file=sys.stderr)
        return 2
    if m["missing"]:
        print("org mode needs these cost.* values (none has a default — column names and the row filter are "
              "this warehouse's, not the kit's): " + ", ".join(m["missing"]) +
              f"\n  columns: `kb.py discover cost.columns` (the DESCRIBE TABLE call) → save the rows as describe.json → "
              f"`cost_report.py propose-columns --describe describe.json` prints the mapping + the config-set line"
              f"\n  filters/email: kb.py config-set cost '{{...}}' (the predicate and the identity are the user's)",
              file=sys.stderr)
        return 2
    col = columns()
    ident = re.compile(r"^[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)*$")
    bad = [f"{k}={v!r}" for k, v in {"spend_table": m["spend_table"], **{f"columns.{k}": v for k, v in col.items()}}.items() if not ident.match(v)]
    if bad:
        print("not a plain SQL identifier (letters, digits, _ $ . only; quoted identifiers are not supported): " + ", ".join(bad), file=sys.stderr)
        return 2
    for k in ("since", "until"):
        v = getattr(a, k)
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            print(f"--{k} wants YYYY-MM-DD, got {v!r}", file=sys.stderr)
            return 2
    email = m["email"].replace("'", "''")
    since = a.since or "2000-01-01"
    until = a.until or date.today().isoformat()
    flt = (m["filters"] or "").strip() or "TRUE"  # free SQL by design — the one value the operator writes as SQL
    where = f"({flt}) AND CAST({col['date']} AS DATE) BETWEEN '{since}' AND '{until}'"
    own = f"""-- own rows, daily × model (billed)
SELECT CAST({col['date']} AS DATE) AS d, {col['model']} AS model,
       SUM({col['cost']}) AS usd, SUM({col['input']}) AS inp, SUM({col['cache_read']}) AS cached,
       SUM({col['cache_write']}) AS cwrite, SUM({col['output']}) AS outp
FROM {m['spend_table']}
WHERE {where} AND LOWER({col['email']}) = LOWER('{email}')
GROUP BY 1, 2 ORDER BY 1, 2;"""
    control = f"""-- control: everyone else, weekly aggregate, weekdays, no identities leave the warehouse
-- (DATE_TRUNC / TO_CHAR ISO week / DAYOFWEEKISO: swap for your engine's equivalents if it rejects them)
SELECT TO_CHAR(DATE_TRUNC('week', {col['date']}), 'IYYY-"W"IW') AS wk,
       COUNT(DISTINCT {col['email']}) AS users,
       COUNT(DISTINCT {col['email']} || CAST({col['date']} AS VARCHAR)) AS user_days,
       SUM({col['cost']}) AS usd, SUM({col['input']}) AS inp, SUM({col['cache_read']}) AS cached,
       SUM({col['cache_write']}) AS cwrite, SUM({col['output']}) AS outp,
       SUM(CASE WHEN LOWER({col['model']}) LIKE '%sonnet%' OR LOWER({col['model']}) LIKE '%haiku%' THEN {col['output']} ELSE 0 END) AS cheap_outp
FROM {m['spend_table']}
WHERE {where} AND LOWER({col['email']}) <> LOWER('{email}') AND DAYOFWEEKISO({col['date']}) <= 5
GROUP BY 1 ORDER BY 1;"""
    print(own)
    print()
    print(control)
    return 0


def _lower_keys(rows: list[dict]) -> list[dict]:
    return [{str(k).lower(): v for k, v in r.items()} for r in rows]


def _num(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _day(v) -> str:
    s = str(v)
    return s[:10]


def cmd_ingest(a) -> int:
    rows = _lower_keys(json.load(open(a.rows)))
    daily = [{"d": _day(r["d"]), "model": str(r.get("model") or ""), "usd": _num(r.get("usd")), "inp": _num(r.get("inp")),
              "cached": _num(r.get("cached")), "cwrite": _num(r.get("cwrite")), "outp": _num(r.get("outp")), "billed": True}
             for r in rows]
    json.dump(daily, open(a.out, "w"), indent=0)
    print(f"{len(daily)} daily rows → {a.out} ({min(r['d'] for r in daily)} .. {max(r['d'] for r in daily)})" if daily else "0 rows")
    if a.control_rows:
        crow = _lower_keys(json.load(open(a.control_rows)))
        ctl = [{"wk": str(r["wk"]), "users": int(_num(r.get("users"))), "user_days": int(_num(r.get("user_days"))),
                "usd": _num(r.get("usd")), "inp": _num(r.get("inp")), "cached": _num(r.get("cached")),
                "cwrite": _num(r.get("cwrite")), "outp": _num(r.get("outp")), "cheap_outp": _num(r.get("cheap_outp"))}
               for r in crow]
        json.dump(ctl, open(a.control_out, "w"), indent=0)
        print(f"{len(ctl)} control weeks → {a.control_out}")
    return 0


# ----------------------------------------------------------------------------- private: transcripts (+ CSV)
def _iter_transcripts() -> list[str]:
    return transcripts.all_transcripts()


def _owner_zone() -> tuple[ZoneInfo, str]:
    """kit_profile.zone(): the owner's zone, or UTC for an unknown WORKSPACE_TZ — the Basis says which."""
    zone, note = profile.zone()
    return zone, (zone.key if not note else f"UTC (WORKSPACE_TZ {profile.tz()!r} is not a known zone)")


def _local_day(ts: str, zone: ZoneInfo) -> str:
    """UTC transcript timestamp → the owner's calendar day; a zone-less stamp is UTC; '' when unparsable."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone).date().isoformat()


def collect_private(since: str | None, until: str | None) -> tuple[list[dict], dict]:
    agg: dict[tuple[str, str], dict] = {}
    seen: set[str] = set()
    n_files = n_req = 0
    zone, zone_label = _owner_zone()
    for path in _iter_transcripts():
        n_files += 1
        for o, m, u, _rid in transcripts.usage_records(path, seen):
            ts = _local_day(str(o.get("timestamp") or ""), zone)
            if not ts or (since and ts < since) or (until and ts > until):
                seen.discard(_rid)  # outside the window: not counted, and a later file may still carry it
                continue
            n_req += 1
            model = str(m.get("model") or "unknown")
            i, cw, cr, out = transcripts.tokens(u)
            p = session_stats.price_for(model)
            usd = (i * p[0] + cw * p[1] + cr * p[2] + out * p[3]) / 1e6
            k = (ts, model)
            row = agg.setdefault(k, {"d": ts, "model": model, "usd": 0.0, "inp": 0, "cached": 0, "cwrite": 0, "outp": 0, "billed": False})
            row["usd"] += usd; row["inp"] += i; row["cached"] += cr; row["cwrite"] += cw; row["outp"] += out
    return sorted(agg.values(), key=lambda r: (r["d"], r["model"])), {"files": n_files, "requests": n_req, "day_boundary_tz": zone_label}


def read_csv(path: str) -> list[dict]:
    """Private-mode CSV: any header spelling in CSV_ALIASES; `cost` optional (estimated when absent)."""
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        keymap = {}
        for h in rd.fieldnames or []:
            norm = re.sub(r"[^a-z0-9]", "", h.lower())
            if norm in CSV_ALIASES:
                keymap[h] = CSV_ALIASES[norm]
        if "d" not in keymap.values():
            raise SystemExit(f"{path}: no date column recognised (accepted: {sorted(set(k for k, v in CSV_ALIASES.items() if v == 'd'))})")
        for r in rd:
            row = {"d": "", "model": "unknown", "usd": None, "inp": 0.0, "cached": 0.0, "cwrite": 0.0, "outp": 0.0}
            for h, f in keymap.items():
                v = r.get(h)
                if f == "d":
                    row["d"] = _day(v)
                elif f == "model":
                    row["model"] = str(v or "csv")
                elif f == "usd":
                    row["usd"] = _num(v) if v not in (None, "") else None
                else:
                    row[f] = _num(v)
            if row["usd"] is None:
                p = session_stats.price_for(row["model"])
                row["usd"] = (row["inp"] * p[0] + row["cwrite"] * p[1] + row["cached"] * p[2] + row["outp"] * p[3]) / 1e6
                row["billed"] = False
            else:
                row["billed"] = True
            out.append(row)
    return out


def cmd_collect_private(a) -> int:
    rows, stats = collect_private(a.since, a.until)
    src = {"transcripts": stats}
    if a.csv:
        csv_rows = read_csv(a.csv)
        if a.since or a.until:
            csv_rows = [r for r in csv_rows if (not a.since or r["d"] >= a.since) and (not a.until or r["d"] <= a.until)]
        # the CSV wins on days it covers (an export is closer to the bill than a transcript estimate)
        covered = {r["d"] for r in csv_rows}
        rows = [r for r in rows if r["d"] not in covered] + csv_rows
        rows.sort(key=lambda r: (r["d"], r["model"]))
        src["csv"] = {"path": a.csv, "rows": len(csv_rows), "days": len(covered),
                      "billed": all(r.get("billed") for r in csv_rows)}
    json.dump({"rows": rows, "source": src}, open(a.out, "w"), indent=0)
    span = f"{rows[0]['d']} .. {rows[-1]['d']}" if rows else "empty"
    print(f"{len(rows)} daily×model rows → {a.out} ({span}); {stats['requests']} API requests in {stats['files']} transcripts"
          + (f"; CSV {src['csv']['rows']} rows over {src['csv']['days']} days" if a.csv else ""))
    return 0


# ----------------------------------------------------------------------------- work units
def _gh(args: list[str]) -> str:
    env = profile.gh_env(dict(os.environ))
    r = subprocess.run(["gh", *args], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed ({r.returncode}): {r.stderr.strip()[:300]}")
    return r.stdout


def _default_since() -> str:
    """Earliest cost.phases start, else 365 days back — never all of history (the search cap is 1000 rows)."""
    starts = [str(p.get("start") or "") for p in (cfg().get("phases") or []) if isinstance(p, dict)]
    starts = [x for x in starts if x]
    return min(starts) if starts else (date.today() - timedelta(days=365)).isoformat()


def _months(since: str, until: str):
    """Yield (first, last) day pairs per calendar month covering since..until."""
    d0, d1 = date.fromisoformat(since), date.fromisoformat(until)
    cur = d0
    while cur <= d1:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur.isoformat(), min(nxt - timedelta(days=1), d1).isoformat()
        cur = nxt


def cmd_work_prs(a) -> int:
    m = resolve_mode()
    login = m["github_login"]
    if not login:
        raise SystemExit("WORKSPACE_GITHUB_LOGIN is not set (plugin `/config` github_login or settings.local.json env) — needed for author:<login>")
    since, until = a.since or _default_since(), a.until or date.today().isoformat()
    scopes = []
    if m["github_org"]:
        scopes.append(f"org:{m['github_org']}")
    scopes += [f"repo:{r}" for r in m["extra_repos"]]
    if not scopes:
        raise SystemExit("neither github.org nor cost.extra_repos is set — nothing to search")
    seen = set()
    rows = []
    for scope in scopes:
        # gh search caps at 1000 rows per query and 30 calls/min: one query per scope × calendar month,
        # and a full page is an error, never a smaller count.
        for lo, hi in _months(since, until):
            q = f"author:{login} is:pr {scope} created:{lo}..{hi}"
            out = _gh(["search", "prs", *q.split(), "--limit", "1000", "--json", "repository,number,createdAt,closedAt,state,isDraft,url"])
            hits = json.loads(out or "[]")
            if len(hits) >= 1000:
                raise SystemExit(f"search cap hit for {scope} {lo}..{hi} (1000 rows) — PR counts would be truncated; "
                                 "narrow the scope (cost.extra_repos) or the window")
            merged_set: set[tuple[str, int]] = set()
            if any(r.get("state") == "closed" for r in hits):
                # older gh reports "closed" for merged PRs too: one is:merged search per scope-month settles it
                mout_ = _gh(["search", "prs", *q.split(), "is:merged", "--limit", "1000", "--json", "repository,number"])
                merged_set = {(r["repository"]["nameWithOwner"], r["number"]) for r in json.loads(mout_ or "[]")}
            for r in hits:
                key = (r["repository"]["nameWithOwner"], r["number"])
                if key in seen:
                    continue
                seen.add(key)
                state = r.get("state", "")
                closed_raw = r.get("closedAt") or ""
                closed = _day(closed_raw) if closed_raw and not closed_raw.startswith("0001") else ""  # gh emits year 0001 for "never"
                merged = ""
                if state == "merged" or (state == "closed" and key in merged_set):
                    merged = closed  # a merged PR's closedAt is its merge time
                rows.append([key[0], r["number"], _day(r["createdAt"]), merged, closed, state])
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["repo", "number", "created", "merged", "closed", "state"])
        w.writerows(sorted(rows, key=lambda x: x[2]))
    print(f"{len(rows)} PRs → {a.out} (since {since})")
    return 0


def cmd_work_tickets(a) -> int:
    kind = profile.get("tracker.kind", "none")
    if kind != "github":
        print(f"tracker.kind is {kind}: run the tracker query through its tool and save a TSV "
              "(key, created, resolved, type, parent) — SKILL.md § Tracker query has the shape", file=sys.stderr)
        return 2
    login = profile.identity("WORKSPACE_GITHUB_LOGIN")
    repos = profile.get("tracker.repos", []) or []
    if not login:
        raise SystemExit("WORKSPACE_GITHUB_LOGIN is not set (plugin `/config` github_login or settings.local.json env) — needed for assignee:<login>")
    if not repos:
        raise SystemExit("tracker.repos is empty — no repo to count closed issues in (a missing config is not 0 tickets)")
    since, until = a.since or _default_since(), a.until or date.today().isoformat()
    rows = []
    for repo in repos:
        out = _gh(["search", "issues", f"repo:{repo}", f"assignee:{login}", "is:issue", f"closed:{since}..{until}",
                   "--limit", "1000", "--json", "number,createdAt,closedAt,labels"])
        hits = json.loads(out or "[]")
        if len(hits) >= 1000:
            raise SystemExit(f"search cap hit for {repo} (1000 rows) — ticket counts would be truncated; pass --since")
        for r in hits:
            rows.append([f"{repo}#{r['number']}", _day(r["createdAt"]), _day(r["closedAt"]),
                         ",".join(l["name"] for l in r.get("labels", [])), ""])
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["key", "created", "resolved", "type", "parent"])
        w.writerows(rows)
    print(f"{len(rows)} closed issues → {a.out}")
    return 0


# ----------------------------------------------------------------------------- report
def _is_wday(d: str) -> bool:
    return date.fromisoformat(d).isoweekday() <= 5


def _iso_week(d: str) -> str:
    y, w, _ = date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def _model_unknown(model: str) -> bool:
    return str(model or "").strip().lower() in ("", "unknown", "csv")


def _cheap(model: str) -> bool:
    m = model.lower()
    return any(c in m for c in CHEAP)


class Bucket:
    def __init__(self, name: str, start: str, end: str):
        self.name, self.start, self.end = name, start, end
        self.usd = self.inp = self.cached = self.cwrite = self.outp = self.cheap_outp = 0.0
        # weekday-only aggregates: every rate AND every ratio uses these, so the decomposition identity
        # $/wday = volume × habit × routing holds exactly; only the `usd` total column includes weekends
        self.wusd = self.winp = self.wcached = self.wcwrite = self.woutp = self.wcheap_outp = self.wunk_outp = 0.0
        self.days: set[str] = set(); self.wdays: set[str] = set()
        self.pr_open = self.pr_merged = self.tk_closed = 0
        self.repos: set[str] = set(); self.epics: set[str] = set()
        self.billed_rows = self.est_rows = 0

    def add(self, r: dict) -> None:
        self.usd += r["usd"]; self.inp += r["inp"]; self.cached += r["cached"]; self.cwrite += r["cwrite"]; self.outp += r["outp"]
        if _cheap(r["model"]):
            self.cheap_outp += r["outp"]
        self.days.add(r["d"])
        if _is_wday(r["d"]):
            self.wdays.add(r["d"])
            self.wusd += r["usd"]; self.winp += r["inp"]; self.wcached += r["cached"]; self.wcwrite += r["cwrite"]; self.woutp += r["outp"]
            if _cheap(r["model"]):
                self.wcheap_outp += r["outp"]
            if _model_unknown(r["model"]):
                self.wunk_outp += r["outp"]
        if r.get("billed"):
            self.billed_rows += 1
        else:
            self.est_rows += 1

    def contains(self, d: str) -> bool:
        return self.start <= d <= self.end

    def metrics(self) -> dict:
        wd = len(self.wdays) or 0
        fixed = (self.winp * FIXED[0] + self.wcwrite * FIXED[1] + self.wcached * FIXED[2] + self.woutp * FIXED[3]) / 1e6
        mout = self.woutp / 1e6
        div = lambda a, b: (a / b) if b else None  # noqa: E731
        if self.billed_rows == 0 and self.est_rows == 0:
            basis = "no data"
        else:
            basis = "billed" if self.est_rows == 0 else ("estimated" if self.billed_rows == 0 else "mixed")
        return {
            "bucket": self.name, "start": self.start, "end": self.end, "days": len(self.days), "wdays": wd,
            "usd": round(self.usd, 2), "usd_per_wday": div(self.wusd, wd),
            "out_mtok": round(self.outp / 1e6, 3), "out_mtok_per_wday": div(mout, wd),
            "in_out": div(self.winp + self.wcwrite + self.wcached, self.woutp),
            "cached_per_out": div(self.wcached, self.woutp),
            "usd_per_mout": div(self.wusd, mout), "fixed_usd_per_mout": div(fixed, mout),
            # None (rendered –) when any weekday output has no model: unknown is not 0% cheap
            "cheap_share": None if self.wunk_outp else div(self.wcheap_outp, self.woutp),
            "unknown_model_outp": round(self.wunk_outp),
            "pr_open": self.pr_open, "pr_merged": self.pr_merged, "repos": len(self.repos), "tk_closed": self.tk_closed,
            "epics": sorted(self.epics),
            "usd_per_pr": div(self.usd, self.pr_open), "usd_per_tk": div(self.usd, self.tk_closed),
            "out_ktok_per_pr": div(self.outp / 1e3, self.pr_open),
            "cost_basis": basis,
        }


def load_daily(path: str) -> tuple[list[dict], dict]:
    o = json.load(open(path))
    if isinstance(o, dict):
        return o.get("rows", []), o.get("source", {})
    return o, {}


def read_tsv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def parse_phase(s: str) -> tuple[str, str, str]:
    m = re.fullmatch(r"([A-Za-z0-9_-]+)=(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", s.strip())
    if not m:
        raise SystemExit(f"--phase wants NAME=YYYY-MM-DD..YYYY-MM-DD, got {s!r}")
    return m.group(1), m.group(2), m.group(3)


def decompose(b: dict, a: dict) -> dict | None:
    """$/wday change from bucket b (before) to a (after) = volume × habit × routing, log-additive."""
    need = ("usd_per_wday", "out_mtok_per_wday", "fixed_usd_per_mout", "usd_per_mout")
    if any(b.get(k) in (None, 0) or a.get(k) in (None, 0) for k in need):
        return None
    vol = a["out_mtok_per_wday"] / b["out_mtok_per_wday"]
    habit = a["fixed_usd_per_mout"] / b["fixed_usd_per_mout"]
    route = (a["usd_per_mout"] / a["fixed_usd_per_mout"]) / (b["usd_per_mout"] / b["fixed_usd_per_mout"])
    total = a["usd_per_wday"] / b["usd_per_wday"]
    lt = math.log(total)
    share = (lambda x: math.log(x) / lt if lt else None)
    avoided = a["out_mtok_per_wday"] * b["usd_per_mout"] - a["usd_per_wday"]
    return {"from": b["bucket"], "to": a["bucket"], "usd_per_wday_change": total - 1, "volume": vol - 1, "habit": habit - 1,
            "routing": route - 1, "share_volume": share(vol), "share_habit": share(habit), "share_routing": share(route),
            "avoided_usd_per_wday": avoided}


def pct(x) -> str:
    return "–" if x is None else f"{x * 100:+.0f}%"


def num(x, nd=0) -> str:
    if x is None:
        return "–"
    return f"{x:,.{nd}f}"


def cmd_report(a) -> int:
    rows, source = load_daily(a.daily)
    if not rows:
        raise SystemExit("daily file has no rows")
    rows.sort(key=lambda r: r["d"])
    first, last = rows[0]["d"], rows[-1]["d"]
    if a.until and a.until < last:
        last = a.until
        rows = [r for r in rows if r["d"] <= last]
    mode = resolve_mode()
    # --- buckets
    buckets: list[Bucket] = []
    last_d = date.fromisoformat(last)
    r7s = (last_d - timedelta(days=6)).isoformat(); p7s = (last_d - timedelta(days=13)).isoformat(); p7e = (last_d - timedelta(days=7)).isoformat()
    if a.view in ("rolling7", "all"):
        buckets += [Bucket("prior-7d", p7s, p7e), Bucket("rolling-7d", r7s, last)]
    phases = [parse_phase(p) for p in (a.phase or [])] or [(str(p["name"]), str(p["start"]), str(p.get("end") or "")) for p in mode["phases"] if isinstance(p, dict) and p.get("name") and p.get("start")]
    if a.view in ("phases", "all") and phases:
        buckets += [Bucket(n, s, e if e else last) for n, s, e in phases]
    if a.view in ("phases", "all") and not phases:
        buckets.append(Bucket("all", first, last))
    weeks: dict[str, Bucket] = {}
    for r in rows:
        for b in buckets:
            if b.contains(r["d"]):
                b.add(r)
        if a.view in ("weekly", "all"):
            wk = _iso_week(r["d"])
            if wk not in weeks:
                d0 = date.fromisoformat(r["d"]); mon = d0 - timedelta(days=d0.isoweekday() - 1)
                weeks[wk] = Bucket(wk, mon.isoformat(), min((mon + timedelta(days=6)).isoformat(), last))
            weeks[wk].add(r)
    allb = buckets + [weeks[k] for k in sorted(weeks)]
    # --- work overlay, capped at the last spend day
    prs = read_tsv(a.prs) if a.prs else []
    tks = read_tsv(a.tickets) if a.tickets else []
    for p in prs:
        c, mg = p.get("created", ""), p.get("merged", "")
        for b in allb:
            if c and c <= last and b.contains(c):
                b.pr_open += 1; b.repos.add(p.get("repo", ""))
            if mg and mg <= last and b.contains(mg):
                b.pr_merged += 1
    for t in tks:
        rs = t.get("resolved", "")
        if not rs or rs > last or "epic" in [x.strip().lower() for x in str(t.get("type", "")).split(",")]:
            continue
        for b in allb:
            if b.contains(rs):
                b.tk_closed += 1
                if t.get("parent"):
                    b.epics.add(t["parent"])
    metrics = {b.name: b.metrics() for b in allb}
    # --- control
    control = []
    if a.control:
        for c in json.load(open(a.control)):
            ud = c.get("user_days") or 0; outp = c.get("outp") or 0
            fixed = (c["inp"] * FIXED[0] + c["cwrite"] * FIXED[1] + c["cached"] * FIXED[2] + outp * FIXED[3]) / 1e6
            control.append({"wk": c["wk"], "users": c.get("users"), "usd_per_user_day": c["usd"] / ud if ud else None,
                            "in_out": (c["inp"] + c["cwrite"] + c["cached"]) / outp if outp else None,
                            "usd_per_mout": c["usd"] / (outp / 1e6) if outp else None,
                            "fixed_usd_per_mout": fixed / (outp / 1e6) if outp else None,
                            "cheap_share": (c.get("cheap_outp") or 0) / outp if outp else None})
    # --- decompositions: rolling vs prior, then every ordered pair of NON-overlapping phases with >1 weekday
    #     (a 1-day changeover bucket such as X never takes part; overlapping phases like P0 ⊃ P0b are not compared)
    decomps = []
    if "prior-7d" in metrics and "rolling-7d" in metrics:
        d = decompose(metrics["prior-7d"], metrics["rolling-7d"]);  d and decomps.append(d)
    ph = sorted((metrics[n] for n, _, _ in phases if n in metrics and metrics[n]["wdays"] > 1), key=lambda x: (x["start"], x["end"]))
    for i, b_ in enumerate(ph):
        for a_ in ph[i + 1:]:
            if a_["start"] > b_["end"]:
                d = decompose(b_, a_);  d and decomps.append(d)
    # --- basis
    basis = {
        "mode": mode["mode"], "cost_basis": mode["cost_basis"], "spend_table": mode["spend_table"], "filters": mode["filters"],
        "source": source or None, "window": f"{first} .. {last}", "rates": "every rate and ratio is weekday-only (ISO 1–5): $/wday, Mout/wday, in/out, $/Mout, cheap%; only the $ total column includes weekends, so $/wday × volume × habit × routing is an exact identity",
        "fixed_prices_usd_per_mtok": mode["fixed_prices"],
        "control": (f"{len(control)} weeks, anonymous aggregate per user-day" if control else "none"),
        "prs": (f"{len(prs)} PRs from GitHub search (author:{mode['github_login'] or '?'}), capped at {last}" if prs else "not supplied"),
        "tickets": (f"{len(tks)} tickets ({mode['tracker']}), closed by resolution date, capped at {last}" if tks else "not supplied"),
        "phases": [{"name": n, "start": s, "end": e} for n, s, e in phases] or "none (rolling 7 days vs the prior 7)",
        "skipped": ["lines changed as a denominator (never)", "per-epic billed cost (not attributable from billing)"]
                   + ([] if control else ["control (private mode has no peer table)"]),
        "names": "none — self-analysis; the control is an aggregate",
    }
    out = {"basis": basis, "buckets": [metrics[b.name] for b in buckets], "weeks": [metrics[k] for k in sorted(weeks)],
           "control": control, "decomposition": decomps}
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    md = render_md(out)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"report → {a.out}" + (f", json → {a.json}" if a.json else ""))
    else:
        print(md)
    return 0


def render_md(o: dict) -> str:
    b = o["basis"]
    L = [f"# Claude Code cost report — {b['window']}", ""]
    L += ["## Basis", "", "| Factor | This run |", "|---|---|",
          f"| Mode | **{b['mode']}** |", f"| Cost | {b['cost_basis']}" + (f" from `{b['spend_table']}` ({b['filters']})" if b['spend_table'] else "") + " |",
          f"| Rates | {b['rates']} |",
          f"| Fixed price | in {b['fixed_prices_usd_per_mtok']['input']} · cache-write {b['fixed_prices_usd_per_mtok']['cache_write']} · cache-read {b['fixed_prices_usd_per_mtok']['cache_read']} · out {b['fixed_prices_usd_per_mtok']['output']} $/Mtok |",
          f"| Control | {b['control']} |", f"| PRs | {b['prs']} |", f"| Tickets | {b['tickets']} |",
          f"| Phases | {b['phases'] if isinstance(b['phases'], str) else ', '.join(p['name'] + ' ' + p['start'] + '..' + p['end'] for p in b['phases'])} |",
          f"| Skipped | {'; '.join(b['skipped'])} |", f"| Names | {b['names']} |", ""]
    hdr = "| Bucket | window | wdays | $ | $/wday | Mout/wday | in/out | $/Mout | fixed $/Mout | cheap% | PRs open | merged | tk closed | $/PR | $/tk | basis |"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    row = lambda m: (f"| {m['bucket']} | {m['start']}..{m['end']} | {m['wdays']} | {num(m['usd'])} | {num(m['usd_per_wday'])} | "
                     f"{num(m['out_mtok_per_wday'], 2)} | {num(m['in_out'])} | {num(m['usd_per_mout'])} | {num(m['fixed_usd_per_mout'])} | "
                     f"{pct(m['cheap_share']).lstrip('+')} | {m['pr_open']} | {m['pr_merged']} | {m['tk_closed']} | {num(m['usd_per_pr'])} | {num(m['usd_per_tk'])} | {m['cost_basis']} |")  # noqa: E731
    foot = ("_`$` is the all-days total; every other column is weekday-only (ISO 1–5), so `$` ÷ output from this "
            "table does not reproduce `$/Mout`._")
    if o["buckets"]:
        L += ["## Buckets (rolling 7 days vs the prior 7, then phases)", "", hdr, sep] + [row(m) for m in o["buckets"]] + ["", foot, ""]
    if o["weeks"]:
        L += ["## Weekly overview", "", hdr, sep] + [row(m) for m in o["weeks"]] + ["", foot, ""]
        ep = [(m["bucket"], m["epics"]) for m in o["weeks"] if m["epics"]]
        if ep:
            L += ["Epics touched (closed tickets' parents): " + "; ".join(f"{w}: {', '.join(e)}" for w, e in ep), ""]
    if o["decomposition"]:
        L += ["## Decomposition of $/wday (cost = volume × habit × routing, log-additive shares)", "",
              "| From → to | Δ $/wday | volume (Mout/wday) | habit (fixed $/Mout) | routing (price ratio) | avoided $/wday |", "|---|---|---|---|---|---|"]
        L += [f"| {d['from']} → {d['to']} | {pct(d['usd_per_wday_change'])} | {pct(d['volume'])} | {pct(d['habit'])} | {pct(d['routing'])} | {num(d['avoided_usd_per_wday'])} |"
              for d in o["decomposition"]] + [""]
    if o["control"]:
        L += ["## Control (everyone else, weekdays, anonymous aggregate)", "", "| Week | users | $/user-day | in/out | $/Mout | fixed $/Mout | cheap% |", "|---|---|---|---|---|---|---|"]
        L += [f"| {c['wk']} | {c['users'] or '–'} | {num(c['usd_per_user_day'])} | {num(c['in_out'])} | {num(c['usd_per_mout'])} | {num(c['fixed_usd_per_mout'])} | {pct(c['cheap_share']).lstrip('+')} |"
              for c in o["control"]] + [""]
    L += ["## Reading the metrics", "",
          "- **in/out** = (input + cache-write + cache-read) / output — prefix dragged per output token; the habit metric, lower is better.",
          "- **fixed $/Mout** reprices every token at one list price, so it moves only with habit, never with model routing.",
          "- **$/Mout** is billed (or estimated) $ per million output tokens — habit × routing.",
          "- **cheap%** = share of output tokens on Sonnet/Haiku — the routing signal.",
          "- **$/PR, $/ticket** are noisy under ~10 units; read them per phase, never on one week.",
          "- **avoided $/wday** = the after-period output priced at the before-period $/Mout, minus actual.", ""]
    return "\n".join(L)


# ----------------------------------------------------------------------------- main
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mode")
    p = sub.add_parser("sql"); p.add_argument("--since"); p.add_argument("--until")
    p = sub.add_parser("propose-columns"); p.add_argument("--describe", required=True, help="DESCRIBE TABLE result as a JSON array of row objects")
    p = sub.add_parser("ingest"); p.add_argument("--rows", required=True); p.add_argument("--control-rows")
    p.add_argument("--out", default="daily.json"); p.add_argument("--control-out", default="control.json")
    p = sub.add_parser("collect-private"); p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--csv"); p.add_argument("--out", default="daily.json")
    p = sub.add_parser("work-prs"); p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--out", default="prs.tsv")
    p = sub.add_parser("work-tickets"); p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--out", default="tickets.tsv")
    p = sub.add_parser("report"); p.add_argument("--daily", required=True); p.add_argument("--control"); p.add_argument("--prs"); p.add_argument("--tickets")
    p.add_argument("--phase", action="append", help="NAME=YYYY-MM-DD..YYYY-MM-DD (repeatable; default from cost.phases)")
    p.add_argument("--view", choices=["rolling7", "weekly", "phases", "all"], default="all"); p.add_argument("--until")
    p.add_argument("--out"); p.add_argument("--json")
    a = ap.parse_args(argv)
    if a.cmd == "mode":
        print(json.dumps(resolve_mode(), indent=1)); return 0
    return {"sql": cmd_sql, "propose-columns": cmd_propose_columns, "ingest": cmd_ingest, "collect-private": cmd_collect_private, "work-prs": cmd_work_prs,
            "work-tickets": cmd_work_tickets, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
