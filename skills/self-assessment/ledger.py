#!/usr/bin/env python3
"""ledger.py — the self-assessment skill's report ledger: one copy-ready card per composed week on a
single self-contained HTML page (published as a private Artifact; URL in `self_assessment.ledger_url`).

    ledger.py card  <weeks/2026-Wnn.md> [--tag TEXT]            # print the card as JSON
    ledger.py build (--page index.html | --init) --week <file>... --out <html>
                    [--title T] [--eyebrow T] [--report-url URL] [--tag TEXT]
    ledger.py check <html>                                        # ids unique, no local paths, ≥3 sections

`card` parses the week file: the frontmatter `title:` gives id + dates, `status:`/banner give the tag
(`composed <date>, week in progress …` while `status: active`; `back-filled <date>` when the
frontmatter says `provenance: back-fill`), the `## <report> update` block gives the sections (`**Heading**`
or `### Heading` lines, `- ` bullets, indented continuations). `build` merges cards into the page's
`<script id="data">` JSON by id (replace or append, sorted by year+week) and refreshes the masthead.
`--init` starts from `ledger-template.html` next to this script. stdlib only, no network — the
publish itself is the Artifact tool (`url` = the configured ledger URL; without it on the first run).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "context-db" / "bin"))
import kit_profile as profile  # noqa: E402

TEMPLATE = HERE / "ledger-template.html"
DATA_RE = re.compile(r'(<script id="data" type="application/json">)(.*?)(</script>)', re.S)
FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
LOCAL_PATH_RE = re.compile(r"(?<![\w/])\.context/|\.worktrees/|/Users/|/home/", re.I)


def frontmatter(text: str) -> dict[str, str]:
    m = FM_RE.match(text)
    fm: dict[str, str] = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm


def week_id(path: Path, fm: dict[str, str]) -> tuple[int, int]:
    m = re.search(r"(\d{4})-W(\d{2})", path.stem) or re.search(r"(\d{4})-W(\d{2})", fm.get("title", ""))
    if not m:
        sys.exit(f"{path}: cannot find YYYY-Wnn in the file name or title")
    return int(m.group(1)), int(m.group(2))


def week_dates(year: int, week: int) -> str:
    mon = dt.date.fromisocalendar(year, week, 1)
    sun = mon + dt.timedelta(days=6)
    return f"{mon:%a %b} {mon.day} – {sun:%a %b} {sun.day}, {sun.year}"


def parse_block(text: str) -> list[dict]:
    """Sections of the report block: the last `## … update` heading to the next `## ` or EOF."""
    heads = [m for m in re.finditer(r"^## .*update.*$", text, re.M | re.I)]
    if not heads:
        sys.exit("no `## … update` block in the week file")
    start = heads[-1].end()
    nxt = re.search(r"^## ", text[start:], re.M)
    block = text[start : start + nxt.start()] if nxt else text[start:]
    sections: list[dict] = []
    cur = None
    for line in block.splitlines():
        h = re.match(r"^\*\*(.+?)\*\*\s*$", line) or re.match(r"^###\s+(.+?)\s*$", line)
        if h:
            cur = {"heading": h.group(1).strip(), "bullets": []}
            sections.append(cur)
            continue
        if cur is None:
            continue
        if re.match(r"^\s*[-*]\s+", line):
            cur["bullets"].append(re.sub(r"^\s*[-*]\s+", "", line).strip())
        elif line.startswith("  ") and cur["bullets"] and line.strip():
            cur["bullets"][-1] += " " + line.strip()
        elif line.strip() and not cur["bullets"]:
            # a heading followed by one plain sentence (the "nothing beyond the above" case)
            cur["bullets"].append(line.strip())
    return [s for s in sections if s["bullets"]]


def make_card(path: Path, extra_tag: str = "") -> dict:
    text = path.read_text(encoding="utf-8")
    fm = frontmatter(text)
    year, week = week_id(path, fm)
    title = fm.get("title", "")
    m = re.search(r"—\s*(.+?)\s*(?:\(|$)", title)
    dates = m.group(1).strip() if m else week_dates(year, week)
    tags: list[str] = []
    sp = re.search(r"\(\s*(sprint:[^)]*)\)", title)
    if sp:
        tags.append(sp.group(1).strip())
    updated = fm.get("updated", "")
    # Provenance comes from the frontmatter, never from prose: a normal week may *mention* back-fill.
    if re.search(r"back-?fill", fm.get("provenance", ""), re.I):
        tags.append(f"back-filled {updated}".strip())
    elif fm.get("status", "") == "active":
        tags.append(f"composed {updated}, week in progress — re-check after the Monday re-run".strip())
    if extra_tag:
        tags.append(extra_tag)
    fri = dt.date.fromisocalendar(year, week, 5)
    return {
        "id": f"W{week:02d}",
        "year": year,
        "dates": dates,
        "tag": " · ".join(tags),
        "nudge": f"{fri:%a %b} {fri.day}",
        "sections": parse_block(text),
    }


def card_year(c: dict) -> int:
    if "year" in c:
        return int(c["year"])
    m = re.search(r"(\d{4})\s*$", c.get("dates", ""))
    return int(m.group(1)) if m else 0


def sort_key(c: dict) -> tuple[int, int]:
    return card_year(c), int(re.sub(r"\D", "", c["id"]) or 0)


def load_page(args) -> str:
    if args.init:
        html = TEMPLATE.read_text(encoding="utf-8")
        esc = lambda t: html_mod.escape(t, quote=True)  # noqa: E731 — every insert is text or an attribute
        title = esc(args.title or "Update Ledger")
        eyebrow = esc(args.eyebrow or profile.identity("WORKSPACE_USER") or "Weekly updates")  # plugin option, else settings.local.json
        link = (
            f'<span><a href="{esc(args.report_url)}" target="_blank" rel="noopener">Open the update form ↗</a></span>'
            if args.report_url
            else ""
        )
        html = html.replace("{{TITLE}}", title).replace("{{EYEBROW}}", eyebrow).replace("{{REPORT_LINK}}", link)
        return html
    return Path(args.page).read_text(encoding="utf-8")


def refresh_masthead(html: str, data: list[dict]) -> str:
    n = len(data)
    html = html.replace("{{N}}", str(n))
    html = re.sub(r'(<b id="m-weeks">)[^<]*(</b>)', rf"\g<1>{n}\g<2>", html)
    html = re.sub(r'(<p class="standfirst">)\S+( weekly updates)', rf"\g<1>{n}\g<2>", html)
    if data:
        first, last = data[0]["dates"], data[-1]["dates"]
        start = re.sub(r"^\w{3}\s+", "", first.split("–")[0]).strip()
        end = last.split("–")[-1].strip()
        end = re.sub(r"^\w{3}\s+", "", end)
        rng = f"{start} – {end}"
        html = html.replace("{{RANGE}}", rng)
        html = re.sub(r'(<div class="mast-meta">\s*<span>)[^<]*(</span>)', rf"\g<1>{rng}\g<2>", html, count=1)
    return html


def build(args) -> None:
    html = load_page(args)
    m = DATA_RE.search(html)
    if not m:
        sys.exit("page has no <script id=\"data\"> block — not a ledger page")
    data = json.loads(m.group(2) or "[]")
    for w in args.week:
        card = make_card(Path(w), args.tag)
        data = [c for c in data if not (c["id"] == card["id"] and card_year(c) == card["year"])]
        data.append(card)
    data.sort(key=sort_key)
    # `</` → `<\/` so a bullet quoting "</script>" cannot terminate the data block.
    payload = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    html = html[: m.start(2)] + payload + "\n" + html[m.end(2) :]
    html = refresh_masthead(html, data)
    problems = check_html(html)
    if problems:
        # Never leave a publishable file behind on a failed check — a session that only sees the
        # file exists would republish it over the live ledger.
        rejected = Path(str(args.out) + ".rejected")
        rejected.write_text(html, encoding="utf-8")
        Path(args.out).unlink(missing_ok=True)
        print("\n".join("⚠ " + p for p in problems), file=sys.stderr)
        sys.exit(f"check failed — nothing written to {args.out} (inspect {rejected}); fix the week file, not the page")
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out} — {len(data)} cards: {', '.join(c['id'] for c in data)}")


def check_html(html: str) -> list[str]:
    m = DATA_RE.search(html)
    if not m:
        return ["no data block"]
    data = json.loads(m.group(2) or "[]")
    problems: list[str] = []
    seen: set[tuple[int, str]] = set()
    for c in data:
        key = (card_year(c), c["id"])
        if key in seen:
            problems.append(f"{c['id']}: duplicate card")
        seen.add(key)
        if len(c.get("sections", [])) < 3:
            problems.append(f"{c['id']}: only {len(c.get('sections', []))} section(s)")
        for s in c.get("sections", []):
            for b in s["bullets"]:
                if LOCAL_PATH_RE.search(b):
                    problems.append(f"{c['id']}: local path in a bullet — {b[:60]}…")
    if "{{" in html:
        problems.append("unfilled {{placeholder}} left in the page")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("card")
    c.add_argument("week")
    c.add_argument("--tag", default="")
    b = sub.add_parser("build")
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--page")
    g.add_argument("--init", action="store_true")
    b.add_argument("--week", nargs="+", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--title", default="")
    b.add_argument("--eyebrow", default="")
    b.add_argument("--report-url", default="")
    b.add_argument("--tag", default="")
    k = sub.add_parser("check")
    k.add_argument("html")
    args = ap.parse_args()
    if args.cmd == "card":
        print(json.dumps(make_card(Path(args.week), args.tag), ensure_ascii=False, indent=1))
    elif args.cmd == "build":
        build(args)
    else:
        problems = check_html(Path(args.html).read_text(encoding="utf-8"))
        print("\n".join(problems) if problems else "OK")
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
