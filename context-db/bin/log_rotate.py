"""log_rotate.py — rotate the long section of a `type: log` doc, which `ctx maintain` leaves alone.

ctx-store's `maintain` rotates only the section that the doc type's schema names (`log.section`). The kit's `log`
type names none, because log docs differ in shape (a `## Day log`, per-run sections, report sections), so a log doc
needs its own answer: a frontmatter field `log_section: <heading>` on the doc. This module is the kit's half of
`maintain` for exactly those docs; `ctx_adapter.py` runs it just before `ctx maintain` (the Bash route) and after a
`ctx_maintain` MCP call (the PostToolUse hook).

For a `type: log` doc outside `archive/` that is over the size guard (30,000 B, the guard `ctx maintain` uses):

* with `log_section: <heading>` — the `keep` newest entries of that `## ` section stay and the older ones move,
  verbatim, into `archive/<folder>-<slug>-log.md` under the same heading (`archive/<slug>-log.md` for a doc at the
  store root; the folder keeps `ops/rot` and `repos/rot` apart); a missing archive doc is created (`type: log`, the
  source's `domain`, `status: active`). An entry is one dated line (`- YYYY-MM-DD …`; a section with no dated lines
  takes every unindented line) plus its continuation lines up to the next entry, so a multi-line entry or a fenced
  block is never cut; the section's order is read from the dated entries, else the `log` type's;
* without it — nothing moves, and the pass says why: add `log_section:` or split the doc by hand.

Writes go through ctx, never around its lock. The archive is written first (`create`), then the moved block is
removed from the source with one `str_replace` of that exact block, which ctx runs under its lock against the
doc's current text: an entry appended in between is kept. If the remove is refused (the block changed, `NO_MATCH`,
or ctx's lock timed out) the pass reads the doc again and, when the same entries are still the ones to move,
removes them without archiving them twice; otherwise it reports the doc as not rotated. A failure between the two
writes leaves the entries in both docs, never in neither; the next pass moves them again, so the archive may hold
a copy twice, but an entry that only looks like an archived one is never dropped.
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
Write = Callable[[str, str], None]   # (key, whole text) — replaces a doc through ctx
Remove = Callable[[str, str], None]  # (key, exact text) — removes it from the doc's current text, once


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


def classify(body: list[str], start: int, end: int) -> list[str]:
    """One tag per line of body[start:end]: `blank`, `comment` (inside an HTML comment), `fence` (a fence line or a
    line inside fenced code) or `text`."""
    tags, comment, fence = [], False, None
    for i in range(start, end):
        line = body[i]
        text = line.strip()
        mark = FENCE.match(line)
        if comment:
            tags.append("comment")
            comment = "-->" not in text
        elif fence is not None:
            tags.append("fence")
            if mark and line.startswith(fence):
                fence = None
        elif mark:
            tags.append("fence")
            fence = mark.group(1)
        elif text.startswith("<!--"):
            tags.append("comment")
            comment = "-->" not in text
        elif text:
            tags.append("text")
        else:
            tags.append("blank")
    return tags


def blocks(body: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """(first line, last line) of every entry in body[start:end], in document order. An entry starts at a dated line
    (`- YYYY-MM-DD …`), or at an unindented line when the section has no dated line, and runs to the line before
    the next entry start, minus trailing blank lines (and, for the last entry, trailing HTML comments). Fenced
    lines never start an entry; text before the first entry is no entry."""
    tags = classify(body, start, end)
    text_lines = [start + k for k, tag in enumerate(tags) if tag == "text"]
    dated = any(DATED.match(body[i]) for i in text_lines)
    starts = [i for i in text_lines
              if (DATED.match(body[i]) if dated else not body[i][:1].isspace())]
    out = []
    for n, first in enumerate(starts):
        last = (starts[n + 1] if n + 1 < len(starts) else end) - 1
        while last > first and tags[last - start] in (("blank",) if n + 1 < len(starts) else ("blank", "comment")):
            last -= 1
        out.append((first, last))
    return out


def order_of(lines: list[str], default: str) -> str:
    """`newest-first` or `oldest-first`, judged by the first and last dated entry; `default` when they do not say."""
    dates = [m.group(1) for m in (DATED.match(line) for line in lines) if m]
    if len(dates) >= 2 and dates[0] != dates[-1]:
        return "newest-first" if dates[0] > dates[-1] else "oldest-first"
    return default


class Cut:
    """What leaves a doc: `old` is the exact text to remove from the source (one contiguous block, its line break
    included), `moved` the entries in their original order, one list of lines each, `order` the section's order."""

    def __init__(self, old: str, moved: list[list[str]], order: str):
        self.old, self.moved, self.order = old, moved, order


def split_old(text: str, section: str, keep: int, default: str) -> Optional[Cut]:
    """The cut that leaves `keep` entries in `section`, on entry boundaries only; None when the section is missing
    or ambiguous, or has no more than `keep` entries."""
    head, body = split_doc(text)
    span = section_span(body, section)
    if span is None:
        return None
    found = blocks(body, *span)
    order = order_of([body[first] for first, _ in found], default)
    old = found[keep:] if order == "newest-first" else found[: max(len(found) - keep, 0)]
    if not old:
        return None
    first, last = old[0][0], old[-1][1]
    block = "\n".join(body[first:last + 1])
    # the line break after the block goes with it; at the very end of a doc, the one before it
    cut = block + "\n" if last + 1 < len(body) else ("\n" + block if first > 0 else block)
    return Cut(cut, [body[a:b + 1] for a, b in old], order)


def add_to_archive(existing: Optional[str], moved: list[list[str]], section: str, order: str, key: str, domain: str,
                   today: str) -> str:
    """The archive doc's text with `moved` added under `## section`: before the first entry for a newest-first
    section, after the last for an oldest-first one. Every entry is added, even one the archive already holds
    word for word: two identical entries are two entries, and a pass cut short between its two writes leaves
    a copy in both docs (see `rotate_store`), never a missing one. A new archive doc is `type: log` with the
    source's domain."""
    lines = [line for e in moved for line in e]
    if existing is None:
        return (f"---\ntitle: Log of {key}\ntype: log\ndomain: {domain}\nstatus: active\nlog_section: {section}\n"
                f"updated: {today}\n---\n\n# Log of {key}\n\n## {section}\n\n" + "\n".join(lines) + "\n")
    head, body = split_doc(existing)
    span = section_span(body, section)
    if span is None:  # the archive has no such section yet (or twice): a new one at the end
        while body and not body[-1].strip():
            body.pop()
        return "\n".join(head + body + ["", f"## {section}", "", *lines, ""])
    found = blocks(body, *span)
    if not found:  # an empty section: one blank line between the heading and the entries
        body[span[0]:span[0]] = ([""] if span[0] < len(body) and body[span[0]].strip() else []) + lines
    elif order == "newest-first":
        body[found[0][0]:found[0][0]] = lines
    else:
        body[found[-1][1] + 1:found[-1][1] + 1] = lines
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


def archive_key(key: str) -> str:
    """Where a log doc's old entries go: `archive/<folder>-<slug>-log`, the folder path joined with dashes
    (`ops/rot` -> `archive/ops-rot-log`; a doc at the store root -> `archive/<slug>-log`), so two docs of one name in
    two folders never share an archive."""
    return "archive/" + key.replace("/", "-") + "-log"


def rotate_store(root: Path, write: Write, remove: Remove, size_guard: int = SIZE_GUARD, keep: int = KEEP_LOG,
                 default_order: str = "newest-first", today: Optional[str] = None,
                 only: Optional[str] = None) -> list[str]:
    """Rotate every oversized `type: log` doc that has a `log_section:` (only the doc `only` names, when given);
    returns the report lines (`archived: …` per rotation, the add-the-field note per doc without the field,
    `log: … — <why>` for one left alone). `write(key, text)` replaces a doc through ctx and `remove(key, old)`
    removes `old` from the doc's current text through ctx; both raise OSError when ctx refuses."""
    today = today or _date.today().isoformat()
    report: list[str] = []
    for key, path in log_docs(root, size_guard):
        if only is not None and key != only:
            continue
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
        target = archive_key(key)
        target_path = root / f"{target}.md"
        try:
            existing = target_path.read_text(encoding="utf-8") if target_path.is_file() else None
            write(target, add_to_archive(existing, cut.moved, section, cut.order, key,
                                         meta.get("domain", "") or "archive", today))
            try:
                remove(key, cut.old)
            except OSError:
                # the doc changed between the read and the remove, or ctx's lock timed out: read it again and, if
                # the very same entries are still the ones to move, remove them now. Those were archived by THIS
                # pass, so they are not archived a second time; an entry that merely looks like an older archived
                # one is a new entry and is always moved.
                again = split_old(path.read_text(encoding="utf-8"), section, keep, default_order)
                if again is None or again.moved != cut.moved:
                    raise
                remove(key, again.old)
        except OSError as e:
            report.append(f"log: {key} not rotated — {e}")
            continue
        report.append(f"archived: {len(cut.moved)} log entries of {key} → {target}")
    return report
