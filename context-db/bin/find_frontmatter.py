#!/usr/bin/env python3
"""Find docs by frontmatter keys only (not body text).

Searches for tag/domain/status in the frontmatter block only (between opening
and closing ---) so a body line that looks like a key does not match.

Usage:
    find_frontmatter.py <context_root> TAG <tag> [STATUS <status>]
    find_frontmatter.py <context_root> DOMAIN <domain> [STATUS <status>]
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

# Skip these directories (same as gen_index.py)
SKIP_DIRS = {"bin", "_templates", "sessions", "handoff", "memory", "state"}
SKIP_FILES = {"INDEX.md", "README.md", "SESSION_INDEX.md"}


def extract_frontmatter(path: Path) -> dict[str, str] | None:
    """Extract frontmatter lines as a dict of key: value pairs.

    Returns None if the file has no frontmatter block.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None

    # Find closing ---
    fm_lines = []
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_lines = lines[1:i]
            break
    else:
        # No closing --- found
        return None

    # Parse frontmatter lines as flat key: value pairs
    fm = {}
    for line in fm_lines:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()

    return fm


def match_tag(fm: dict[str, str], tag: str) -> bool:
    """Check if tag is in the tags field."""
    tags_str = fm.get("tags", "")
    if not tags_str:
        return False
    # tags can be [a, b] or "a, b" format
    tags_str = tags_str.strip().strip("[]\"'")
    tags = [t.strip().strip("\"'") for t in tags_str.split(",") if t.strip()]
    return tag in tags


def match_domain(fm: dict[str, str], domain: str) -> bool:
    """Check if domain matches."""
    doc_domain = fm.get("domain", "").strip()
    return doc_domain == domain


def match_status(fm: dict[str, str], status: str) -> bool:
    """Check if status matches."""
    doc_status = fm.get("status", "").strip()
    return doc_status == status


def find_docs(root: Path, filter_type: str, filter_value: str, status: str | None = None) -> list[str]:
    """Find docs matching the filter criteria.

    Args:
        root: Context root directory
        filter_type: "TAG" or "DOMAIN"
        filter_value: The tag or domain to match
        status: Optional status filter

    Returns:
        List of file paths relative to root, sorted
    """
    results = []

    for dirpath, dirnames, filenames in os.walk(str(root)):
        # Skip engine directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for fn in filenames:
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue

            full_path = Path(dirpath) / fn
            fm = extract_frontmatter(full_path)
            if not fm:
                continue

            # Check primary filter
            if filter_type == "TAG":
                if not match_tag(fm, filter_value):
                    continue
            elif filter_type == "DOMAIN":
                if not match_domain(fm, filter_value):
                    continue
            else:
                continue

            # Check status filter if provided
            if status is not None:
                if not match_status(fm, status):
                    continue

            # Get relative path
            rel_path = full_path.relative_to(root)
            results.append(str(rel_path))

    return sorted(results)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    root = Path(argv[1])
    if not root.exists():
        print(f"error: context root does not exist: {root}", file=sys.stderr)
        return 1

    filter_type = argv[2]  # TAG or DOMAIN
    filter_value = argv[3]
    status = None

    # Check for STATUS filter
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
