#!/usr/bin/env python3
"""`make find`: the docs whose frontmatter carries a tag or a domain, optionally in one status. Only the
frontmatter is read — a body line that looks like a key never matches — and the walk skips what the index skips.
Usage: find_frontmatter.py <context-root> TAG|DOMAIN <value> [STATUS <status>]"""
from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontmatter as fm  # noqa: E402  — the one frontmatter parser
from gen_index import SKIP_DIRS, SKIP_FILES, norm_tags  # noqa: E402  — one walk, one tag reading


def matches(meta: dict[str, str], key: str, want: str) -> bool:
    """A tag matches as a whole entry of the list, any other key as its whole value."""
    if key == "tags":
        return want in norm_tags(meta.get("tags", ""))
    return meta.get(key, "").strip() == want


def find_docs(root: Path, wanted: dict[str, str]) -> list[str]:
    """Paths under `root`, sorted, of the docs whose frontmatter matches every key in `wanted`."""
    hits = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue
            path = Path(dirpath) / fn
            meta = fm.load_flat(path, errors="replace")
            if meta and all(matches(meta, k, v) for k, v in wanted.items()):
                hits.append(str(path.relative_to(root)))
    return sorted(hits)


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) not in (3, 5) or args[1] not in ("TAG", "DOMAIN") or (len(args) == 5 and args[3] != "STATUS"):
        print(__doc__.splitlines()[-1], file=sys.stderr)
        return 2
    root = Path(args[0])
    if not root.is_dir():
        print(f"find: no such content root: {root}", file=sys.stderr)
        return 1
    wanted = {"tags" if args[1] == "TAG" else "domain": args[2]}
    if len(args) == 5:
        wanted["status"] = args[4]
    for hit in find_docs(root, wanted):
        print(hit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
