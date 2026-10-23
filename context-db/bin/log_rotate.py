"""log_rotate.py — rotate the long section of a `type: log` doc, which `ctx maintain` leaves alone.

ctx-store's `maintain` rotates only the section that the doc type's schema names (`log.section`). The kit's `log`
type names none, because log docs differ in shape (a `## Day log`, per-run sections, report sections), so a log doc
needs its own answer: a frontmatter field `log_section: <heading>` on the doc. This module is the kit's half of
`maintain` for exactly those docs; `ctx_adapter.py` runs it just before `ctx maintain` (the Bash route) and after a
`ctx_maintain` MCP call (the PostToolUse hook).

For a `type: log` doc outside `archive/` that is over the size guard (30,000 B, the guard `ctx maintain` uses):

* with `log_section: <heading>` — the `keep` newest entries of that `## ` section stay (an entry is a line that is
  neither blank nor inside an HTML comment, as ctx counts it; the section's order is read from its dated entries,
  `- YYYY-MM-DD …`, else the `log` type's) and the older ones move, verbatim, into `archive/<slug>-log.md` under
  the same heading; a missing archive doc is created (`type: log`, the source's `domain`, `status: active`);
* without it — nothing moves, and the pass says why: add `log_section:` or split the doc by hand.

Writes go through `ctx create` (the caller's `write`), so the audit trail and the store lock stay ctx's. The
archive is written first: a failure between the two writes leaves the entries in both docs, never in neither.
Stdlib only; no CLI — imported by `ctx_adapter.py`.
"""
from __future__ import annotations
import os
import re
from datetime import date as _date
from pathlib import Path
from typing import Callable, Optional

import frontmatter as fm

SIZE_GUARD = 30000  # bytes: ctx-store's default `maintain.size_guard`, the size past which a doc is rotated
KEEP_LOG = 6        # entries left in the doc: the kit's `maintain.keep_log`
SKIP_DIRS = {"archive", "bin", "_templates", "sessions", "handoff", "memory", "state"}  # gen_index's, plus archive/
SKIP_FILES = {"INDEX.md", "README.md", "SESSION_INDEX.md"}
HEADING = re.compile(r"^(#{1,6}) +(.*?)\s*$")
DATED = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b")
FENCE = re.compile(r"^(```|~~~)")
Write = Callable[[str, str], None]


def split_doc(text: str) -> tuple[list[str], list[str]]:
    """(the frontmatter block's lines, fences included; the body's lines) — an empty head for a doc without one."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[: i + 1], lines[i + 1:]
    return [], lines


def fields(text: str) -> dict[str, str]:
    """The top-level `key: value` scalars of a doc's frontmatter, unquoted (frontmatter.py's flat parser)."""
    parts = fm.split(text)
    return {k: fm.unquote(v) for k, v in fm.parse_flat(parts[0]).items()} if parts else {}


def headings(body: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, text) of every heading outside fenced code."""
    found, fence = [], None
    for i, line in enumerate(body):
        mark = FENCE.match(line)
        if mark:
            if fence is None:
                fence = mark.group(1)
            elif line.startswith(fence):
                fence = None
            continue
        m = HEADING.match(line) if fence is None else None
        if m:
            found.append((i, len(m.group(1)), m.group(2)))
    return found


def section_span(body: list[str], name: str) -> Optional[tuple[int, int]]:
    """(first line after the `## name` heading, the line after the section's last line); None when the heading is
    missing or appears twice (ctx leaves an ambiguous section alone, and so does this)."""
    found = headings(body)
    hits = [k for k, (_, level, text) in enumerate(found) if level == 2 and text == name]
    if len(hits) != 1:
        return None
    end = len(body)
    for index, level, _ in found[hits[0] + 1:]:
        if level <= 2:
            end = index
            break
    return found[hits[0]][0] + 1, end


def entries(body: list[str], start: int, end: int) -> list[int]:
    """Indices of the entry lines in body[start:end]: neither blank nor inside an HTML comment."""
    out, comment = [], False
    for i in range(start, end):
        text = body[i].strip()
        if comment or text.startswith("<!--"):
            comment = "-->" not in text
        elif text:
            out.append(i)
    return out


def order_of(lines: list[str], default: str) -> str:
    """`newest-first` or `oldest-first`, judged by the first and last dated entry; `default` when they do not say."""
    dates = [m.group(1) for m in (DATED.match(line) for line in lines) if m]
    if len(dates) >= 2 and dates[0] != dates[-1]:
        return "newest-first" if dates[0] > dates[-1] else "oldest-first"
    return default


def split_old(text: str, section: str, keep: int, default: str) -> Optional[tuple[str, list[str], str]]:
    """(the doc text without its old entries, the moved lines in their original order, the section's order); None
    when the section is missing or ambiguous, or has no more than `keep` entries."""
    head, body = split_doc(text)
    span = section_span(body, section)
    if span is None:
        return None
    found = entries(body, *span)
    order = order_of([body[i] for i in found], default)
    old = found[keep:] if order == "newest-first" else found[: max(len(found) - keep, 0)]
    if not old:
        return None
    gone = set(old)
    kept = [line for i, line in enumerate(body) if i not in gone]
    return "\n".join(head + kept), [body[i] for i in old], order


def add_to_archive(existing: Optional[str], moved: list[str], section: str, order: str, key: str, domain: str,
                   today: str) -> str:
    """The archive doc's text with `moved` added under `## section`: before the first entry for a newest-first
    section, after the last for an oldest-first one. A new archive doc is `type: log` with the source's domain."""
    if existing is None:
        return (f"---\ntitle: Log of {key}\ntype: log\ndomain: {domain}\nstatus: active\nlog_section: {section}\n"
                f"updated: {today}\n---\n\n# Log of {key}\n\n## {section}\n\n" + "\n".join(moved) + "\n")
    head, body = split_doc(existing)
    span = section_span(body, section)
    if span is None:  # the archive has no such section yet (or twice): a new one at the end
        while body and not body[-1].strip():
            body.pop()
        return "\n".join(head + body + ["", f"## {section}", "", *moved, ""])
    found = entries(body, *span)
    if not found:  # an empty section: one blank line between the heading and the entries
        body[span[0]:span[0]] = ([""] if span[0] < len(body) and body[span[0]].strip() else []) + moved
    elif order == "newest-first":
        body[found[0]:found[0]] = moved
    else:
        body[found[-1] + 1:found[-1] + 1] = moved
    return "\n".join(head + body)


def log_docs(root: Path, size_guard: int = SIZE_GUARD) -> list[tuple[str, Path]]:
    """(key, path) of every `type: log` doc outside archive/ and the kit's operational folders that is over
    `size_guard` bytes. The key is the path under `root` without `.md`. Only an oversized file is read."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue
            try:
                if p.stat().st_size <= size_guard:
                    continue
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if fields(text).get("type") == "log":
                out.append((p.relative_to(root).with_suffix("").as_posix(), p))
    return out


def no_field_note(key: str, size: int) -> str:
    """What the pass says about an oversized `log` doc that names no section to rotate."""
    return (f"log: {key} is {size // 1024} KB, over the size guard, and `ctx maintain` cannot rotate it: it has "
            "no `log_section:` — add `log_section: <heading>` to its frontmatter (the `## ` section whose oldest "
            "entries move to archive/), or split the doc by hand")


def rotate_store(root: Path, write: Write, size_guard: int = SIZE_GUARD, keep: int = KEEP_LOG,
                 default_order: str = "newest-first", today: Optional[str] = None) -> list[str]:
    """Rotate every oversized `type: log` doc that has a `log_section:`; returns the report lines (`archived: …`
    per rotation, the add-the-field note per doc without the field, `log: … — <why>` for one left alone).
    `write(key, text)` replaces a doc through ctx and raises OSError when ctx refuses."""
    today = today or _date.today().isoformat()
    report: list[str] = []
    for key, path in log_docs(root, size_guard):
        text = path.read_text(encoding="utf-8")
        meta = fields(text)
        section = meta.get("log_section", "")
        if not section:
            report.append(no_field_note(key, path.stat().st_size))
            continue
        cut = split_old(text, section, keep, default_order)
        if cut is None:
            report.append(f"log: {key} is over the size guard but its `## {section}` section is missing, "
                          f"ambiguous or holds no more than {keep} entries — nothing to rotate")
            continue
        rest, moved, order = cut
        target = f"archive/{key.rsplit('/', 1)[-1]}-log"
        target_path = root / f"{target}.md"
        try:
            existing = target_path.read_text(encoding="utf-8") if target_path.is_file() else None
            write(target, add_to_archive(existing, moved, section, order, key, meta.get("domain", "") or "archive",
                                         today))
            write(key, rest)
        except OSError as e:
            report.append(f"log: {key} not rotated — {e}")
            continue
        report.append(f"archived: {len(moved)} log entries of {key} → {target}")
    return report
