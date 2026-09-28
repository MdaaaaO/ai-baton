#!/usr/bin/env python3
"""readme-check.py — measure a README.md / CONTRIBUTING.md against the repo-docs house style.

Prints one line per rule (`ok` / `FAIL` / `warn`) and exits 1 when any rule fails, so the numbers — not an
impression — say whether a front page is readable. The kind is taken from the file name (CONTRIBUTING* →
contributing, anything else → readme) unless --kind says otherwise. Stdlib only.

Usage: readme-check.py <file.md> [--kind readme|contributing] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# The limits live here once; SKILL.md § 2 explains each one.
LIMITS = {
    "readme": {"total_words": 1500, "pitch_words": 80, "section_words": 350, "badges": (3, 6)},
    "contributing": {"total_words": 800, "pitch_words": 60, "section_words": 300, "badges": (0, 0)},
}
PARAGRAPH_WORDS = (90, 150)   # warn / fail: a paragraph past these is a wall — split it, or make it a list or table
TABLE_ROW_CHARS = 400  # a table row past this is a paragraph hiding in a cell
LINE_CHARS = 200       # a source line longer than this is unreadable in a diff (tables and badge lines exempt)
FENCE = re.compile(r"^\s*(```|~~~)")
BADGE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\s[^>]*\bsrc=[\"']https?://[^\"']+[\"']")
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*)")
WORD = re.compile(r"[\w`'’-]+")
INSTALL_CMD = re.compile(r"\b(pip|pipx|uv|uvx|npm|npx|brew|cargo|go|claude|git)\s+(install|i|add|tool|clone|plugin)\b|curl .*\|\s*(ba)?sh")
README_NEEDS = {
    "install": re.compile(r"install|quick ?start|getting started|setup|usage", re.I),
    "license": re.compile(r"licen[cs]e", re.I),
}
CONTRIB_NEEDS = {
    "short version": re.compile(r"short version|tl;?dr|quick|in short", re.I),
}


def words(text: str) -> int:
    return len(WORD.findall(re.sub(r"\]\([^)]*\)|https?://\S+|<[^>]+>", " ", text)))


def parse(text: str) -> dict:
    lines = text.splitlines()
    in_code = False
    first_code = None          # index of the first fence line
    first_h2 = None
    prose: list[tuple[int, str]] = []   # (line index, line) outside code blocks
    sections: list[list] = []  # [title, words]
    for i, line in enumerate(lines):
        if FENCE.match(line):
            if first_code is None and not in_code:
                first_code = i
            in_code = not in_code
            continue
        if in_code:
            continue
        prose.append((i, line))
        m = HEADING.match(line)
        if m and len(m.group(1)) == 2:
            first_h2 = i if first_h2 is None else first_h2
            sections.append([m.group(2).strip(), 0])
        elif sections and not m:
            sections[-1][1] += words(line)
    # the pitch: prose between the title/badges and the first code block or H2, whichever comes first
    stop = min(x for x in (first_code, first_h2, len(lines)) if x is not None)
    head_block = "\n".join(l for i, l in prose if i < stop)  # title + badge row, before the pitch runs into a section
    intro = [l for i, l in prose if i < stop and not HEADING.match(l) and not BADGE.search(l)]
    pitch = [l for l in intro if not re.match(r"\s*([-*]|\d+\.)\s|\s{2,}\S", l)]
    first_block = []
    if first_code is not None:
        for l in lines[first_code + 1:]:
            if FENCE.match(l):
                break
            first_block.append(l)
    paragraphs, cur = [], []
    for _, l in prose:
        if not l.strip() or HEADING.match(l) or l.lstrip().startswith(("|", "-", "*", ">", "<")) or re.match(r"\s*\d+\.", l):
            if cur:
                paragraphs.append(" ".join(cur))
            cur = []
            continue
        cur.append(l.strip())
    if cur:
        paragraphs.append(" ".join(cur))
    long_lines = [i + 1 for i, l in prose if len(l) > LINE_CHARS and not l.lstrip().startswith("|") and not BADGE.search(l)]
    fat_rows = [i + 1 for i, l in prose if l.lstrip().startswith("|") and len(l) > TABLE_ROW_CHARS]
    return {
        "total_words": sum(words(l) for _, l in prose),
        "pitch_words": words(" ".join(pitch)),
        "badges": len(BADGE.findall(head_block)),
        "first_code_line": None if first_code is None else first_code + 1,
        "first_screen_code": first_code is not None and first_code < 40,
        "sections": sections,
        "longest_paragraph": max((words(p) for p in paragraphs), default=0),
        "long_lines": long_lines,
        "fat_rows": fat_rows,
        "install_in_first_block": bool(INSTALL_CMD.search("\n".join(first_block))),
        "intro_text": "\n".join(intro),
        "titles": [s[0] for s in sections],
    }


def check(stats: dict, kind: str) -> list[tuple[str, str, str]]:
    lim = LIMITS[kind]
    out = []

    def rule(ok: bool, name: str, detail: str, soft: bool = False):
        out.append(("ok" if ok else ("warn" if soft else "FAIL"), name, detail))

    rule(stats["total_words"] <= lim["total_words"], "length", f"{stats['total_words']} words (≤ {lim['total_words']}; the rest belongs in docs/)")
    rule(0 < stats["pitch_words"] <= lim["pitch_words"], "pitch", f"{stats['pitch_words']} words before the first code block or section (1–{lim['pitch_words']})")
    lo, hi = lim["badges"]
    if hi:
        rule(lo <= stats["badges"] <= hi, "badges", f"{stats['badges']} (want {lo}–{hi}: CI, version, license at least)")
    if kind == "readme":
        rule(stats["first_screen_code"], "first code block", f"line {stats['first_code_line']} (want one within the first 40 lines)")
    big = [f"{t} ({w})" for t, w in stats["sections"] if w > lim["section_words"]]
    rule(not big, "section size", ", ".join(big) or f"every section ≤ {lim['section_words']} words", soft=True)
    warn_at, fail_at = PARAGRAPH_WORDS
    lp = stats["longest_paragraph"]
    para_detail = f"longest {lp} words (aim ≤ {warn_at}, fail > {fail_at})"
    if lp <= warn_at:
        rule(True, "paragraphs", f"longest {lp} words (≤ {warn_at})")
    elif lp <= fail_at:
        rule(False, "paragraphs", para_detail, soft=True)
    else:
        rule(False, "paragraphs", para_detail)
    rule(not stats["fat_rows"], "table rows", f"{len(stats['fat_rows'])} row(s) over {TABLE_ROW_CHARS} chars, first at line {stats['fat_rows'][0]} — a cell is not a paragraph" if stats["fat_rows"] else f"all ≤ {TABLE_ROW_CHARS}")
    rule(not stats["long_lines"], "line length", f"{len(stats['long_lines'])} prose line(s) over {LINE_CHARS} chars, first at line {stats['long_lines'][0]}" if stats["long_lines"] else f"all ≤ {LINE_CHARS}", soft=True)
    needs = README_NEEDS if kind == "readme" else CONTRIB_NEEDS
    for name, rx in needs.items():
        if any(rx.search(t) for t in stats["titles"]):
            rule(True, f"section: {name}", "present")
        elif name == "install" and stats["install_in_first_block"]:
            rule(True, f"section: {name}", "the first code block installs it")
        elif name == "short version" and rx.search(stats["intro_text"]):
            rule(True, f"section: {name}", "in the intro")
        else:
            rule(False, f"section: {name}", "missing")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--kind", choices=sorted(LIMITS))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path = Path(a.file)
    kind = a.kind or ("contributing" if path.name.upper().startswith("CONTRIBUTING") else "readme")
    stats = parse(path.read_text(encoding="utf-8"))
    results = check(stats, kind)
    if a.json:
        print(json.dumps({"kind": kind, "stats": stats, "results": results}, ensure_ascii=False, indent=2))
    else:
        for status, name, detail in results:
            print(f"{status:4}  {name:18} {detail}")
    return 1 if any(s == "FAIL" for s, _, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
