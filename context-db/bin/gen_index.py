#!/usr/bin/env python3
"""Generate INDEX.md — the materialized catalog of the context DB.

Walks every *.md doc under the content root (except the engine files), reads its
YAML frontmatter "row" (title/type/domain/tags/status/updated), and emits a
grouped, sorted catalog. INDEX.md is generated — never hand-edit it; the kit's
PostToolUse hook (ctx_adapter.py post-tool-use-async) reruns this after every change
under the content root, and `make -C $BATON/context-db index` runs it by hand.

The content root is kit_profile.context_root() (CONTEXT_ROOT, which the Makefile
sets from CONTEXT, else the default in docs/layout.md's "Content root" paragraph).

Stdlib only (no PyYAML): frontmatter is a flat `key: value` block, parsed here.
"""
from __future__ import annotations
import os
import re
from pathlib import Path
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontmatter as fm  # noqa: E402  — the one frontmatter parser
import kit_profile  # noqa: E402  — the one content-root resolver
from fsutil import atomic_write  # noqa: E402  — same dir; INDEX.md is a store file, never a torn write

ROOT = str(kit_profile.context_root())
# Files/dirs that are engine, not content:
SKIP_DIRS = {"bin", "_templates", "sessions", "handoff", "memory", "state"}  # sessions/ = live session registry, on-call/handoff/ = handoff toolkit state (the toolkit itself lives outside the kit) + rendered page artifacts (operational, not knowledge)
SKIP_FILES = {"INDEX.md", "README.md", "SESSION_INDEX.md"}
REQUIRED = ("title", "type", "domain", "status", "updated")
GENERATED_LINE = re.compile(r"^_\d+ docs · generated \d{4}-\d{2}-\d{2}_$")  # the one line verify ignores when diffing INDEX.md


def parse_frontmatter(path: str) -> dict | None:
    """Return the frontmatter dict, or None if the file has no (terminated) `---` block."""
    return fm.load_flat(Path(path))


def norm_tags(raw: str) -> list[str]:
    raw = raw.strip().strip("[]")
    if not raw:
        return []
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT)


def collect() -> tuple[list[dict], list[str]]:
    rows, problems = [], []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue
            full = os.path.join(dirpath, fn)
            try:
                meta = parse_frontmatter(full)
            except UnicodeDecodeError as e:
                problems.append(f"{rel(full)}: not valid UTF-8 (byte {e.object[e.start:e.start + 1]!r} at offset {e.start}) — re-save the file as UTF-8")
                continue
            except OSError as e:
                problems.append(f"{rel(full)}: cannot read — {e.strerror}")
                continue
            if meta is None:
                problems.append(f"{rel(full)}: no frontmatter block")
                continue
            missing = [k for k in REQUIRED if not meta.get(k)]
            if missing:
                problems.append(f"{rel(full)}: missing {', '.join(missing)}")
            meta["_path"] = rel(full)
            meta["_tags"] = norm_tags(meta.get("tags", ""))
            rows.append(meta)
    return rows, problems


def render(rows: list[dict]) -> str:
    domains: dict[str, list[dict]] = {}
    for r in rows:
        domains.setdefault(r.get("domain", "?"), []).append(r)

    out = []
    out.append("# `.context/` — INDEX")
    out.append("")
    out.append(
        "> **Generated file — do not hand-edit.** The kit's PostToolUse hook regenerates it after "
        "every change under `.context/` (by hand: `make -C $BATON/context-db index`). This is the "
        "queryable catalog of the context DB; each row is one doc's frontmatter. Read this "
        "first (cheap), then open only the leaf docs your task needs."
    )
    out.append("")
    out.append(f"_{len(rows)} docs · generated {date.today().isoformat()}_")
    out.append("")

    for domain in sorted(domains):
        docs = sorted(
            domains[domain],
            key=lambda r: (r.get("status") == "archived", r.get("updated", "")),
            reverse=False,
        )
        # active first (updated desc), archived last
        active = sorted([d for d in docs if d.get("status") != "archived"],
                        key=lambda r: r.get("updated", ""), reverse=True)
        archived = sorted([d for d in docs if d.get("status") == "archived"],
                          key=lambda r: r.get("updated", ""), reverse=True)
        docs = active + archived

        out.append(f"## {domain}")
        out.append("")
        out.append("| Doc | Title | Type | Status | Tags | Updated |")
        out.append("|---|---|---|---|---|---|")
        for r in docs:
            tags = " ".join(f"`{t}`" for t in r["_tags"])
            title = r.get("title", "").replace("|", "\\|")
            out.append(
                f"| [`{r['_path']}`]({r['_path']}) | {title} | {r.get('type','')} "
                f"| {r.get('status','')} | {tags} | {r.get('updated','')} |"
            )
        out.append("")
    return "\n".join(out)


def main() -> int:
    rows, problems = collect()
    index_path = os.path.join(ROOT, "INDEX.md")
    atomic_write(index_path, render(rows) + "\n")
    print(f"wrote {rel(index_path)} — {len(rows)} docs across "
          f"{len({r.get('domain') for r in rows})} domains")
    if problems:
        print("\nWARNINGS (fix with `make verify`):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
