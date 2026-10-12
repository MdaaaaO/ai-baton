#!/usr/bin/env python3
"""mission.py — build the mission-control page from the .context/ store.

Reads the store's structured sources (session registry and ledger, doc
frontmatter, priorities, queues, memory pointers; never a transcript) into one
deterministic data.json, gates it for leaks, and inlines it in a
self-contained index.html. No model turn, no network, no clock in the data:
an unchanged store gives an unchanged data.json and the same page sha.

  build [--out DIR] [--template FILE] [--force]
      Write data.json, build.json, index.html, last.sha and a .gitignore
      holding `*` into DIR (default: state/mission-control in the content
      root). A secret shape or an e-mail address anywhere in data.json stops
      the build: exit 1, nothing written, one `<section> · line <n> · <what>
      · <redacted>` line per hit on stdout. When last.sha already names the
      page it would write, it prints `unchanged` and writes nothing; --force
      rebuilds regardless.
  diff [--out DIR] [--template FILE]
      The same computation without writing or gating: exit 0 when last.sha
      matches, 1 when the page changed or was never built.
  open [--out DIR]
      Print the absolute path of index.html, exit 1 when it is not built.
      The engine never starts a browser.

Template: --template FILE, else the kit's skills/mission-control/page.tpl.html
when it exists, else a minimal built-in page. Tokens: {{DATA_JSON}} and
{{BUILD_JSON}} (script-safe JSON for the <script id="data"> and
<script id="build"> blocks of type application/json), {{KIT_VERSION}}, and
{{part:NAME}} (parts/NAME beside the template, inlined as is, one level deep).
The built-in page also uses {{AS_OF}} and {{DATA_PRETTY}}.

The content root is CONTEXT_ROOT, else the resolver in kit_profile (see
docs/layout.md). Exit codes: 0 ok, 1 changed / absent / leak hit, 2 usage or
I/O error (one stderr line, never a traceback).
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontmatter  # noqa: E402  — same dir: the one frontmatter parser
import gen_index  # noqa: E402  — same dir: the doc catalog walk
import gen_sessions  # noqa: E402  — same dir: the session registry parser
import kit_profile as profile  # noqa: E402  — same dir: the one content-root resolver
import leak_shapes  # noqa: E402  — same dir: the secret shapes and their redaction
from fsutil import atomic_write  # noqa: E402  — same dir
from session import OPEN_PRS_HEADING  # noqa: E402  — the writer owns the session doc's section headings
from session_stats import SPEND_BASIS  # noqa: E402  — one spend-basis wording

SCHEMA = 1
OUT_SUBDIR = "state/mission-control"  # under the content root; its own .gitignore keeps it out of the store's git
STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"
DATE_FMT = "%Y-%m-%d"
LOCAL_NO_ZONE = profile.LOCAL_TIME_FMT.rpartition(" ")[0]  # LOCAL_TIME_FMT without its trailing zone name
LARGE_BYTES = 30 * 1024
CRONS_HEADING = "## Crons"
OUTCOME_HEADING = "## Outcome of the walk"
TEMPLATE_REL = "skills/mission-control/page.tpl.html"
EMAIL_SHAPE = r"(?<![\w.+-])(?!git@)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"  # git@ stays: an SSH remote is not an address
OUTPUTS = ("data.json", "build.json", "index.html")
LAST_SHA = "last.sha"
GITIGNORE = ".gitignore"
PR_LAST = 10  # PR review rows shown under queues.pr_review.last

ROOT = profile.context_root()
ZONE = profile.zone()[0]  # the display zone; UTC when the configured one is unknown

# Every source under the content root, by reader name; also the pointers.sources list.
PATHS = {
    "catalog": ".",
    "index": "INDEX.md",
    "kit_health": "kit-health",
    "ledger": "sessions/_ledger.md",
    "memory": "memory/MEMORY.md",
    "pr_review": "state/pr-review/ledger.jsonl",
    "priorities": "reference/priorities.md",
    "session_index": "SESSION_INDEX.md",
    "sessions": "sessions",
    "sign_queue": "state/sign-queue",
}

# sessions/_ledger.md header label -> data.json key (the writer is session.py's _ledger_append)
LEDGER_COLUMNS = {
    "Ended": "ended", "Session": "session", "Epic": "epic", "Turns": "turns", "Hours": "hours",
    "Ctx peak": "ctx_peak", "Cache-read": "cache_read", "Out": "out", "~$": "usd", "Compactions": "compactions",
    "PRs": "prs", "Tickets": "tickets", "Sign jobs": "sign_jobs", "Drafts": "drafts",
}
LEDGER_NUMBERS = ("turns", "hours", "ctx_peak", "cache_read", "out", "compactions", "prs", "tickets", "sign_jobs", "drafts")

NUMBER_RE = re.compile(r"\s*~?\$?(\d[\d,]*(?:\.\d+)?|\.\d+)([kKmM]?)\s*")
USD_SPLIT_RE = re.compile(r"\(\s*(\d[\d.,]*\s*\+\s*\d[\d.,]*)\s*\)")
REF_RE = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+|[\w.-]+)?#(?P<num>\d+)$")  # owner/repo#N, repo#N, #N
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")  # KEY-123
URL_RE = re.compile(r"^https?://[^\s<>\"']+$")
PR_REF_RE = re.compile(
    r"(?<![\w/.-])(?:https?://github\.com/(?P<url_repo>[\w.-]+/[\w.-]+)/pull/(?P<url_num>\d+)"
    r"|(?P<repo>(?:[\w.-]+/)?[\w.-]+)?#(?P<num>\d+))\b")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*?)\s*$")
MEMORY_RE = re.compile(r"^\s*[-*]\s+\[(?P<title>[^\]]+)\]\((?P<file>[^)\s]+)\)\s*(?:[—–-]+\s*(?P<hook>.*?))?\s*$")
INDEX_LINE_RE = re.compile(r"^_(\d+) docs · generated (\d{4}-\d{2}-\d{2})_$")
SESSION_INDEX_LINE_RE = re.compile(
    r"^_generated (.+?) · (\d+) active · (\d+) ended listed · (\d+) ended with no prompt \(not listed\)_$")
HEALTH_LOG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
PART_RE = re.compile(r"\{\{part:([^{}]*)\}\}")
PART_NAME_RE = re.compile(r"^\w[\w.-]*$")
TOKEN_RE = re.compile(r"\{\{(DATA_JSON|BUILD_JSON|AS_OF|DATA_PRETTY)\}\}")

# The built-in page: no template installed, still a complete, self-contained, request-free page.
BUILTIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mission control</title>
<style>body{font:14px/1.4 system-ui,sans-serif;margin:1.5rem}pre{white-space:pre-wrap;word-break:break-word}</style>
</head>
<body>
<h1>Mission control — data as of {{AS_OF}}</h1>
<p>Kit {{KIT_VERSION}} · the built-in page; the full page replaces it once the template is installed.</p>
<pre>{{DATA_PRETTY}}</pre>
<script id="data" type="application/json">{{DATA_JSON}}</script>
<script id="build" type="application/json">{{BUILD_JSON}}</script>
</body>
</html>
"""


class Fail(Exception):
    """A usage or I/O problem: main prints it as the one stderr line and exits 2."""


# ── time ──────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_utc(value) -> datetime | None:
    try:
        return datetime.strptime(value, STAMP_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime(STAMP_FMT)


def norm_stamp(value) -> str | None:
    """A UTC stamp, or a bare date read as its UTC midnight, as a stamp; None for anything else."""
    t = parse_utc(value)
    if t is None:
        try:
            t = datetime.strptime(value, DATE_FMT).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    return stamp(t)


def parse_local(text) -> str | None:
    """A time as profile.local_str renders it (LOCAL_TIME_FMT in the display zone) back to a UTC stamp; None when
    `text` is not in that shape. The zone name ends the text: at the repeated hour of a DST change it says which
    of the two the writer meant."""
    base, _, name = (text or "").strip().rpartition(" ")
    try:
        t = datetime.strptime(base, LOCAL_NO_ZONE)
    except ValueError:
        return None
    for fold in (0, 1):
        local = t.replace(tzinfo=ZONE, fold=fold)
        if local.tzname() == name:
            return stamp(local)
    return stamp(t.replace(tzinfo=ZONE))


def is_stale(heartbeat: str, now: datetime) -> bool:
    """Whether a stored heartbeat is more than gen_sessions.STALE_HOURS old at `now` (UTC when naive). The page
    applies this rule when it is opened: data.json stores no age, so it stays the same until the store changes.
    A heartbeat that is not a UTC stamp is not stale, as in gen_sessions."""
    t = parse_utc(heartbeat)
    if t is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600.0 > gen_sessions.STALE_HOURS


# ── numbers ───────────────────────────────────────────────────────────────────────────────────────────────────
def tidy(n: float):
    """`n` as an int when it is whole, else rounded to two places — so a sum never prints float noise."""
    r = round(n, 2)
    return int(r) if r == int(r) else r


def number(cell, whole: bool = True):
    """The number in a stats or ledger cell: `12`, `0.5`, `345k`, `1.2M`, `~$44`. None for `-`, an empty cell or
    text. `whole=False` reads only the leading number, so `48 (23+25)` is 48."""
    text = cell or ""
    m = NUMBER_RE.match(text)
    if not m or (whole and m.end() != len(text)):
        return None
    scale = {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)
    return tidy(float(m.group(1).replace(",", "")) * scale)


# ── links ─────────────────────────────────────────────────────────────────────────────────────────────────────
def conf(key: str) -> str:
    value = profile.get(key, "")
    return value if isinstance(value, str) else ""


def config_repos() -> list[str]:
    value = profile.get("tracker.repos", [])
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    return [str(r) for r in value if str(r).strip()] if isinstance(value, list) else []


def full_repo(token: str) -> str | None:
    """`owner/repo` for a repo as a ref writes it: as is when it names an owner, under github.org when it is a bare
    name, the first tracker.repos entry when it is empty (`#12`). None when that cannot be told."""
    token = (token or "").strip()
    if "/" in token:
        return token
    if token:
        org = conf("github.org")
        return f"{org}/{token}" if org else None
    repos = config_repos()
    return repos[0] if repos else None


def pr_url(repo: str | None, number_: int | None) -> str | None:
    return f"https://github.com/{repo}/pull/{number_}" if repo and "/" in repo and number_ else None


def issue_url(ref: str) -> str | None:
    """The tracker URL of an epic or ticket reference, built from tracker.url_template ({key}, and {repo} for a
    GitHub-shaped one); None when it cannot be resolved. `owner/repo#12` is a GitHub issue wherever the template
    comes from, so a template without {repo} falls back to github.com for it; a `KEY-123` key needs a template
    with no {repo} to fill."""
    words = (ref or "").split()
    token = words[0].rstrip(",;") if words else ""
    if not token or token == "-":
        return None
    if URL_RE.match(token):
        return token
    template = conf("tracker.url_template")
    m = REF_RE.match(token)
    if m:
        repo = full_repo(m.group("repo") or "")
        if repo is None or "/" not in repo:
            return None
        if "{repo}" in template:
            return template.replace("{repo}", repo).replace("{key}", m.group("num"))
        return f"https://github.com/{repo}/issues/{m.group('num')}"
    if KEY_RE.match(token) and template and "{repo}" not in template:
        return template.replace("{key}", token)
    return None


def open_prs(text: str) -> list[dict]:
    """The PRs a session's `## Open PRs` section lists: one entry per line that names one (`owner/repo#12`,
    `repo#12`, `#12` or a full PR URL); `none`, an empty section and lines naming no PR give nothing."""
    out = []
    for line in text.split("\n"):
        item = BULLET_RE.sub("", line).strip()
        if not item or item.lower().rstrip(".") == "none":
            continue
        m = PR_REF_RE.search(item)
        if not m:
            continue
        if m.group("url_repo"):
            repo, num = m.group("url_repo"), int(m.group("url_num"))
        else:
            written = m.group("repo") or ""
            repo, num = full_repo(written) or written, int(m.group("num"))
        out.append({"repo": repo, "number": num, "url": pr_url(repo, num), "text": item})
    return out


# ── readers ───────────────────────────────────────────────────────────────────────────────────────────────────
def read_text(path: Path) -> str:
    """The file's text, "" when it is absent or unreadable — a missing source is an empty section, never an error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def note(seen: list[str], value) -> None:
    """Record a source timestamp (a UTC stamp or a date) for as_of."""
    s = norm_stamp(value)
    if s:
        seen.append(s)


def split_repos(value: str) -> list[str]:
    return [t for t in (x.strip("'\"") for x in re.split(r"[,\s]+", (value or "").strip().strip("[]"))) if t]


def session_files(directory: Path, skip: tuple = ()) -> list[Path]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [directory / n for n in names
            if n.endswith(".md") and n[0] not in "._" and n not in skip and (directory / n).is_file()]


def session_entry(path: Path, seen: list[str], problems: list[str]) -> dict | None:
    rel = gen_sessions.rel_from_root(str(path))
    try:
        m = gen_sessions.parse(str(path))
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None  # swept between the listing and the read
    except (OSError, ValueError) as e:
        problems.append(f"{rel}: cannot read — {getattr(e, 'strerror', None) or 'not valid UTF-8'}")
        return None
    parts = frontmatter.split(text)
    body = parts[1] if parts else text
    stats = m.get("stats", "")
    heartbeat = m.get("heartbeat", "")
    note(seen, heartbeat)
    return {
        "name": m["session"],
        "ref": m.get("ref", ""),
        "status": m.get("status", ""),
        "epic": m.get("epic", ""),
        "epic_url": issue_url(m.get("epic", "")),
        "repos": split_repos(m.get("repos", "")),
        "working_on": m.get("working_on", ""),
        "responsibilities": m.get("responsibilities", ""),
        "heartbeat": heartbeat,
        "heartbeat_local": profile.local_str(heartbeat, ZONE),
        "stats": stats,
        "cost_usd": number(gen_sessions.cost_split(stats)[0], whole=False),
        "has_prompt": gen_sessions.has_next_prompt(m),
        "prompt": m["_next"],
        "open_prs": open_prs(gen_sessions.section(body, OPEN_PRS_HEADING)),
        "crons": [ln for ln in gen_sessions.section(body, CRONS_HEADING).split("\n") if ln.strip()],
        "path": rel,
    }


def newest_first(entries: list[dict]) -> list[dict]:
    """Heartbeat descending, then name (the sort is stable, so the name order survives equal heartbeats)."""
    return sorted(sorted(entries, key=lambda s: s["name"]), key=lambda s: s["heartbeat"], reverse=True)


def read_sessions(seen: list[str]) -> dict:
    problems: list[str] = []
    live_dir = ROOT / PATHS["sessions"]
    live = [e for e in (session_entry(p, seen, problems) for p in session_files(live_dir)) if e]
    archived = [e for e in (session_entry(p, seen, problems)
                            for p in session_files(live_dir / "archive", skip=("INDEX.md",))) if e]
    active = newest_first([s for s in live if s["status"] != "ended"])
    ended = newest_first([s for s in live if s["status"] == "ended"])
    return {
        "active": active,
        "ended": ended,
        "archived": newest_first(archived),
        "counts": {"active": len(active), "ended": len(ended),
                   "ended_no_prompt": sum(1 for s in ended if not s["has_prompt"]), "archived": len(archived)},
        "problems": sorted(problems),
    }


def ended_utc(cell: str) -> str | None:
    t = parse_utc(cell)
    return stamp(t) if t else parse_local(cell)


def read_ledger(seen: list[str]) -> list[dict]:
    """sessions/_ledger.md as rows, in file order (newest last). The header names the columns; no header, no rows."""
    header: list[str | None] | None = None
    rows = []
    for line in read_text(ROOT / PATHS["ledger"]).split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in (line[1:-1] if line.endswith("|") else line[1:]).split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if header is None:
            if cells[0] == "Ended":
                header = [LEDGER_COLUMNS.get(c) for c in cells]
            continue
        raw = dict(zip(header, cells))
        if not (raw.get("session") or raw.get("ended")):
            continue
        epic = "" if raw.get("epic", "") == "-" else raw.get("epic", "")
        usd_cell = raw.get("usd", "")
        split = USD_SPLIT_RE.search(usd_cell)
        row = {k: number(raw.get(k, "")) for k in LEDGER_NUMBERS}
        row.update({
            "ended": raw.get("ended", ""),
            "ended_utc": ended_utc(raw.get("ended", "")),
            "session": raw.get("session", "").strip("`"),
            "epic": epic,
            "epic_url": issue_url(epic),
            "usd": number(usd_cell, whole=False),
            "usd_split": split.group(1).replace(" ", "") if split else "",
        })
        note(seen, row["ended_utc"])
        rows.append(row)
    return rows


def cost_block(ledger: list[dict], active: list[dict], as_of: str | None) -> dict:
    def tally(rows: list[dict]) -> dict:
        return {"rows": len(rows), "usd": tidy(sum(r["usd"] or 0 for r in rows))}

    def within(days: int) -> list[dict]:
        end = parse_utc(as_of)
        if end is None:
            return []
        start = end - timedelta(days=days)
        return [r for r in ledger if r["ended_utc"] and start <= parse_utc(r["ended_utc"]) <= end]

    epics: dict[str, list[dict]] = {}
    for r in ledger:
        epics.setdefault(r["epic"], []).append(r)
    by_epic = [{"epic": e, "epic_url": issue_url(e), **tally(rs)} for e, rs in epics.items()]
    by_epic.sort(key=lambda e: (-e["usd"], e["epic"]))
    return {
        "basis": SPEND_BASIS,
        "ledger": ledger,
        "totals": {**tally(ledger), "turns": tidy(sum(r["turns"] or 0 for r in ledger)),
                   "hours": tidy(sum(r["hours"] or 0 for r in ledger)), "by_epic": by_epic,
                   "last_7d": tally(within(7)), "last_30d": tally(within(30))},
        "live": [{"name": s["name"], "stats": s["stats"], "cost_usd": s["cost_usd"]} for s in active],
    }


def read_catalog(seen: list[str]) -> dict:
    rows, problems = gen_index.collect()
    docs = []
    for r in rows:
        try:
            size = os.path.getsize(os.path.join(gen_index.ROOT, r["_path"]))
        except OSError:
            size = 0
        docs.append({
            "path": r["_path"].replace(os.sep, "/"),
            "title": frontmatter.unquote(r.get("title", "")),
            "type": frontmatter.unquote(r.get("type", "")),
            "domain": frontmatter.unquote(r.get("domain", "")),
            "status": frontmatter.unquote(r.get("status", "")),
            "tags": r["_tags"],
            "updated": frontmatter.unquote(r.get("updated", "")),
            "bytes": size,
            "large": size > LARGE_BYTES,
        })
        note(seen, docs[-1]["updated"])
    docs.sort(key=lambda d: d["path"])
    domains: dict[str, dict] = {}
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for d in docs:
        dom = domains.setdefault(d["domain"] or "?", {"domain": d["domain"] or "?", "docs": 0, "bytes": 0})
        dom["docs"] += 1
        dom["bytes"] += d["bytes"]
        by_status[d["status"] or "?"] = by_status.get(d["status"] or "?", 0) + 1
        by_type[d["type"] or "?"] = by_type.get(d["type"] or "?", 0) + 1
    return {"docs": docs, "domains": [domains[k] for k in sorted(domains)], "problems": sorted(problems),
            "counts": {"docs": len(docs), "by_status": by_status, "by_type": by_type}}


def read_priorities() -> dict:
    path = ROOT / PATHS["priorities"]
    items, section, fenced = [], "", False
    for line in read_text(path).split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif fenced:
            continue
        elif line.startswith("## "):
            section = line[3:].strip()
        else:
            m = CHECKBOX_RE.match(line)
            if m:
                items.append({"text": m.group("text"), "done": m.group("mark") != " ", "section": section})
    return {"present": path.is_file(), "path": PATHS["priorities"], "items": items}


def read_pr_review(seen: list[str]) -> dict:
    rows = []
    for line in read_text(ROOT / PATHS["pr_review"]).split("\n"):
        try:
            row = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
            note(seen, row.get("ts"))
    by_status: dict[str, int] = {}
    last = []
    for r in rows:
        key = str(r.get("status") or "?")
        by_status[key] = by_status.get(key, 0) + 1
    for r in rows[-PR_LAST:]:
        num = int(str(r["pr"])) if str(r.get("pr", "")).isdigit() else None
        repo = str(r.get("repo") or "")
        last.append({"repo": repo, "pr": num, "url": pr_url(full_repo(repo) if repo else None, num),
                     "head": str(r.get("head") or ""), "status": str(r.get("status") or ""), "ts": str(r.get("ts") or "")})
    return {"path": PATHS["pr_review"], "rows": len(rows), "by_status": by_status, "last": last}


def read_sign_queue(seen: list[str]) -> dict:
    directory = ROOT / PATHS["sign_queue"]
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".sh"))
    except OSError:
        names = []
    jobs = []
    for n in names:
        try:
            if not (directory / n).is_file():
                continue
            mtime = stamp(datetime.fromtimestamp((directory / n).stat().st_mtime, timezone.utc))
        except OSError:
            continue
        jobs.append({"name": n, "mtime_utc": mtime})
        note(seen, mtime)
    return {"path": PATHS["sign_queue"], "jobs": jobs}


def outcome_of(text: str) -> str:
    """The walk log's `## Outcome of the walk …` heading line and the first line under it, as written."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith(OUTCOME_HEADING):
            body = next((b.strip() for b in lines[i + 1:] if b.strip()), "")
            return line.strip() if not body or body.startswith("#") else f"{line.strip()}\n{body}"
    return ""


def read_kit_health(seen: list[str]) -> dict:
    directory = ROOT / PATHS["kit_health"]
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    logs = [n for n in names if HEALTH_LOG_RE.match(n)]
    latest = None
    if logs:
        latest = {"path": f"{PATHS['kit_health']}/{logs[-1]}", "date": logs[-1][:10],
                  "outcome": outcome_of(read_text(directory / logs[-1]))}
        note(seen, logs[-1][:10])
    stamps = []
    for n in names:
        if n.startswith("HEALTH-") and n.endswith(".md"):
            try:
                meta = frontmatter.load_flat(directory / n, errors="replace") or {}
            except OSError:
                continue
            stamps.append((frontmatter.unquote(meta.get("updated", "")), n))
    newest = max(stamps) if stamps else None
    if newest:
        note(seen, newest[0])
    return {"latest": latest,
            "stamp": {"path": f"{PATHS['kit_health']}/{newest[1]}", "updated": newest[0]} if newest else None}


def read_pointers(seen: list[str]) -> dict:
    memory = []
    for line in read_text(ROOT / PATHS["memory"]).split("\n"):
        m = MEMORY_RE.match(line)
        if m:
            memory.append({"title": m.group("title"), "file": m.group("file"), "hook": m.group("hook") or ""})
    index = session_index = None
    for line in read_text(ROOT / PATHS["index"]).split("\n"):
        m = INDEX_LINE_RE.match(line.strip())
        if m:
            index = {"docs": int(m.group(1)), "generated": m.group(2)}
            note(seen, m.group(2))
            break
    for line in read_text(ROOT / PATHS["session_index"]).split("\n"):
        m = SESSION_INDEX_LINE_RE.match(line.strip())
        if m:
            session_index = {"generated": m.group(1), "active": int(m.group(2)), "ended_listed": int(m.group(3)),
                             "ended_no_prompt": int(m.group(4))}
            note(seen, parse_local(m.group(1)))
            break
    sources = [{"name": k, "path": v, "present": (ROOT / v).exists()} for k, v in sorted(PATHS.items())]
    return {"memory": memory, "index": index, "session_index": session_index, "sources": sources}


def links_block() -> dict:
    return {"tracker_kind": conf("tracker.kind"), "tracker_url_template": conf("tracker.url_template"),
            "github_org": conf("github.org"), "repos": config_repos()}


def assemble() -> dict:
    """The whole of data.json as a dict: a pure function of the store (and the display-zone and tracker config)."""
    seen: list[str] = []
    sessions = read_sessions(seen)
    ledger = read_ledger(seen)
    catalog = read_catalog(seen)
    priorities = read_priorities()
    queues = {"pr_review": read_pr_review(seen), "sign_queue": read_sign_queue(seen),
              "kit_health": read_kit_health(seen)}
    pointers = read_pointers(seen)
    as_of = max(seen) if seen else None
    return {
        "schema": SCHEMA,
        "as_of": as_of,
        "tz": ZONE.key,
        "stale_hours": gen_sessions.STALE_HOURS,
        "links": links_block(),
        "sessions": sessions,
        "cost": cost_block(ledger, sessions["active"], as_of),
        "catalog": catalog,
        "priorities": priorities,
        "queues": queues,
        "pointers": pointers,
    }


# ── gate ──────────────────────────────────────────────────────────────────────────────────────────────────────
def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def gate(data: dict) -> list[str]:
    """One `<section> · line <n> · <what> · <redacted value>` per secret shape or e-mail address in data.json,
    scanned section by section; `n` counts the lines of that section's own JSON. Workspace paths are allowed."""
    shapes = [(re.compile(rx), what) for rx, what in leak_shapes.SECRET_SHAPES]
    shapes.append((re.compile(EMAIL_SHAPE), "e-mail address"))
    hits = []
    for name in sorted(data):
        for n, line in enumerate(dumps(data[name]).split("\n"), 1):
            for what, value in leak_shapes.line_hits(line, shapes):
                hits.append(f"{name} · line {n} · {what} · {leak_shapes.redact(what, value)}")
    return hits


# ── page ──────────────────────────────────────────────────────────────────────────────────────────────────────
def kit_version() -> str:
    try:
        return (profile.KIT / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def inline(text: str, parts_dir: Path | None) -> str:
    """`text` with each {{part:NAME}} replaced by parts/NAME and {{KIT_VERSION}} by the kit version: everything in
    the page that does not come from the store. A part is inserted as is (one level: its own tokens of that kind
    stay)."""
    def part(m: re.Match) -> str:
        name = m.group(1).strip()
        try:
            if parts_dir is None or not PART_NAME_RE.match(name):
                raise OSError(name)
            return (parts_dir / name).read_text(encoding="utf-8")
        except (OSError, ValueError):
            raise Fail(f"template part missing: parts/{name}")
    return PART_RE.sub(part, text).replace("{{KIT_VERSION}}", html.escape(kit_version()))


def resolve_template(arg: str | None) -> tuple[str, str]:
    """(the template with its parts and version inlined, its name for build.json): --template FILE, else the kit's
    own page template, else the built-in page. The name is `builtin`, a path relative to the kit, or a bare file
    name — never an absolute path."""
    path = Path(arg) if arg else profile.KIT / TEMPLATE_REL
    if not arg and not path.is_file():
        return inline(BUILTIN_PAGE, None), "builtin"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        raise Fail(f"cannot read template {path.name}: {getattr(e, 'strerror', None) or 'not valid UTF-8'}")
    try:
        label = str(path.resolve().relative_to(profile.KIT))
    except ValueError:
        label = path.name
    return inline(text, path.parent / "parts"), label


def script_safe(text: str) -> str:
    """JSON text that cannot end its <script> block early: every `<` (only ever inside a string) and the two line
    separators a script parser treats as newlines become their \\u escapes, which JSON reads back unchanged."""
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render(template: str, data_text: str, build_text: str, as_of: str | None) -> str:
    values = {"DATA_JSON": script_safe(data_text), "BUILD_JSON": script_safe(build_text),
              "AS_OF": html.escape(as_of or "n/a"), "DATA_PRETTY": html.escape(data_text)}
    return TOKEN_RE.sub(lambda m: values[m.group(1)], template)  # one pass: inserted text is never scanned again


def sha(*texts: str) -> str:
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode("utf-8"))
    return h.hexdigest()


def store_sha() -> str | None:
    """The store's git HEAD when it is its own repository and context.sync is not off; None otherwise (no git,
    no commit, a store that sits inside another repository)."""
    try:
        import ctx_sync  # same dir; imported here because it needs fcntl, which a non-POSIX host lacks
    except ImportError:
        return None
    git = ctx_sync.Git(ROOT)
    try:
        if ctx_sync.mode_of(git)[0] == "off":
            return None
        return git.out("rev-parse", "HEAD", timeout=10) or None
    except (OSError, subprocess.SubprocessError):
        return None


# ── commands ──────────────────────────────────────────────────────────────────────────────────────────────────
def out_dir(arg: str | None) -> Path:
    return Path(os.path.abspath(arg if arg else ROOT / OUT_SUBDIR))


def read_last(out: Path) -> str:
    return read_text(out / LAST_SHA).strip()


def complete(out: Path) -> bool:
    """Whether every output is on disk, with the .gitignore that keeps the directory out of the store's git."""
    return all((out / n).is_file() for n in (*OUTPUTS, GITIGNORE))


def plan(a: argparse.Namespace) -> tuple[Path, dict, str, str, str, str]:
    """(out dir, data, data.json text, template text, template name, page sha) — what build and diff both start from."""
    if not ROOT.is_dir():
        raise Fail(f"content root not found: {ROOT}")  # a mistyped root must not become an empty page in a new tree
    out = out_dir(a.out)
    template, label = resolve_template(a.template)
    data = assemble()
    data_text = dumps(data)
    return out, data, data_text, template, label, sha(data_text, template)


def cmd_build(a: argparse.Namespace) -> int:
    out, data, data_text, template, label, page_sha = plan(a)
    hits = gate(data)
    if hits:
        print("\n".join(hits))
        print(f"mission: {len(hits)} leak hit(s) — nothing written", file=sys.stderr)
        return 1
    if read_last(out) == page_sha and complete(out) and not a.force:
        print(f"mission: unchanged {page_sha[:12]} {out}")
        return 0
    build_text = dumps({"data_sha": sha(data_text), "page_sha": page_sha,
                        "generated_at": stamp(datetime.now(timezone.utc)), "kit_version": kit_version(),
                        "store_sha": store_sha(), "template": label})
    if not (out / GITIGNORE).is_file():
        atomic_write(str(out / GITIGNORE), "*\n")  # first: whatever else lands here is invisible to the store's git
    atomic_write(str(out / "data.json"), data_text)
    atomic_write(str(out / "build.json"), build_text)
    atomic_write(str(out / "index.html"), render(template, data_text, build_text, data["as_of"]))
    atomic_write(str(out / LAST_SHA), page_sha + "\n")  # last: a build cut short never looks finished
    print(f"mission: built {out / 'index.html'} {page_sha[:12]}")
    return 0


def cmd_diff(a: argparse.Namespace) -> int:
    out, _data, _text, _template, _label, page_sha = plan(a)
    last = read_last(out)
    if last == page_sha and complete(out):
        print(f"mission: unchanged {page_sha[:12]} {out}")
        return 0
    if not last:
        print(f"mission: not built {out}")
    elif last == page_sha:
        print(f"mission: incomplete {out}")
    else:
        print(f"mission: changed {last[:12]} -> {page_sha[:12]} {out}")
    return 1


def cmd_open(a: argparse.Namespace) -> int:
    page = out_dir(a.out) / "index.html"
    if not page.is_file():
        print(f"mission: nothing built at {page} — run build first", file=sys.stderr)
        return 1
    print(page)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{build,diff,open}")
    for name, fn, text in (("build", cmd_build, "write the page and its data (gated, skipped when unchanged)"),
                           ("diff", cmd_diff, "exit 0 when the built page is current, 1 when not"),
                           ("open", cmd_open, "print the path of the built index.html")):
        sp = sub.add_parser(name, help=text, description=text)
        sp.set_defaults(fn=fn)
        sp.add_argument("--out", metavar="DIR", default=None,
                        help="output directory (default: state/mission-control in the content root)")
        if name != "open":
            sp.add_argument("--template", metavar="FILE", default=None,
                            help="page template (default: the kit's, else a built-in page)")
        if name == "build":
            sp.add_argument("--force", action="store_true", help="rebuild even when last.sha matches")
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except Fail as e:
        print(f"mission: {e}", file=sys.stderr)
    except OSError as e:
        print(f"mission: {e.strerror or e}{f': {e.filename}' if e.filename else ''}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
