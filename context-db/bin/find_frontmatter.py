#!/usr/bin/env python3
"""Find docs by frontmatter keys only (not body text)."""
from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontmatter as fm  # noqa: E402 — the one frontmatter parser
from gen_index import SKIP_DIRS, SKIP_FILES, norm_tags  # noqa: E402


def matches(meta: dict[str, str], key: str, want: str) -> bool:
    """Check if frontmatter key matches the wanted value."""
    if key == "tags":
        tags = norm_tags(meta.get("tags", ""))
        return want in tags
    else:
        return meta.get(key, "").strip() == want


def find_docs(root: Path, filter_type: str, filter_value: str, status: str | None = None) -> list[str]:
    """Find docs matching the filter criteria (TAG or DOMAIN, optional STATUS)."""
    results = []

    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for fn in filenames:
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue

            full_path = Path(dirpath) / fn
            try:
                fm_meta = fm.load_flat(full_path)
            except UnicodeDecodeError:
                continue
            if not fm_meta:
                continue

            # Check primary filter
            filter_key = "tags" if filter_type == "TAG" else filter_type.lower()
            if not matches(fm_meta, filter_key, filter_value):
                continue

            # Check status filter if provided
            if status is not None and not matches(fm_meta, "status", status):
                continue

            rel_path = full_path.relative_to(root)
            results.append(str(rel_path))

    return sorted(results)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: find_frontmatter.py <context_root> TAG <tag> [STATUS <status>]", file=sys.stderr)
        print("       find_frontmatter.py <context_root> DOMAIN <domain> [STATUS <status>]", file=sys.stderr)
        return 2

    root = Path(argv[1])
    if not root.exists():
        print(f"error: context root does not exist: {root}", file=sys.stderr)
        return 1

    filter_type = argv[2]
    filter_value = argv[3]
    status = None

    if len(argv) >= 6 and argv[4] == "STATUS":
        status = argv[5]

    if filter_type not in ("TAG", "DOMAIN"):
        print(f"error: unknown filter type: {filter_type}", file=sys.stderr)
        return 2

    results = find_docs(root, filter_type, filter_value, status)
    for path in results:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
