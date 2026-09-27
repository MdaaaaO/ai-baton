#!/usr/bin/env python3
"""migrate_frontmatter.py — move the kit's bookkeeping keys under `metadata:` (Agent Skills spec).

Rewrites the frontmatter of every skill (`skills/*/SKILL.md`) and agent (`agents/*.md`), or of the files
given, so that `version`, `updated`, `reviewed`, `requires` and `facts` live under `metadata:` as
double-quoted strings, and a unit with `requires` carries a `compatibility:` line:

    version: 6                          metadata:
    requires: [slack, airflow]    →       version: "6"
    facts: [slack.channel x, a.b]         requires: "airflow,slack"
                                          facts: "slack.channel x,a.b"
                                        compatibility: "Designed for Claude Code; needs airflow, slack (systems.*)"

Everything else — the body, every other frontmatter key and its order, comments — is kept byte for byte.
Idempotent: a migrated file is left alone (exit 0, "unchanged"). `--dry-run` prints what would change
and writes nothing; `--check` is `--dry-run` with exit 3 when anything is pending (a CI gate).
Stdlib only.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontmatter as fmt  # noqa: E402

KIT = fmt.KIT
MOVED = fmt.KIT_META_KEYS                              # top-level → metadata, in this order
HEAD = ("name", "description", "license", "compatibility")  # spec identity keys stay first, in this order


def compatibility_for(requires: list[str]) -> str:
    return f'"Designed for Claude Code; needs {", ".join(requires)} (systems.*)"'


def quote(v: str) -> str:
    return '"' + fmt.unquote(v).replace('"', '\\"') + '"'


def migrate_text(text: str) -> tuple[str, list[str]]:
    """(new text, one line per change) — the text unchanged and [] when nothing is pending."""
    parts = fmt.split(text)
    if parts is None:
        return text, []
    lines, body = parts
    changes: list[str] = []

    # 1. read the block into an ordered list of (key, raw lines) entries, keeping unknown lines in place
    entries: list[tuple[str | None, list[str]]] = []
    for line in lines:
        m = fmt.KEY.match(line)
        if m:
            entries.append((m.group(1), [line]))
        elif entries and (line.startswith(" ") or not line.strip()):
            entries[-1][1].append(line)  # continuation / nested line belongs to the previous key
        else:
            entries.append((None, [line]))  # a comment or something the parser leaves alone
    top = {k: v for k, v in entries if k}

    # 2. what moves, as plain strings
    moved: dict[str, str] = {}
    for key in MOVED:
        if key in top:
            raw = fmt.strip_comment(fmt.KEY.match(top[key][0]).group(2) or "")
            if key in ("requires", "facts"):
                items = fmt.parse_csv(raw)
                moved[key] = ",".join(sorted(items) if key == "requires" else items)
            else:
                moved[key] = fmt.unquote(raw)

    # 3. the existing metadata block (moved keys never overwrite what is already there)
    existing = fmt.meta(fmt.parse_lines(lines))
    meta_lines = [ln for ln in top.get("metadata", [])[1:] if ln.strip()]
    for key, val in moved.items():
        if key in existing:
            changes.append(f"{key}: dropped (metadata.{key} already set)")
        else:
            meta_lines.append(f"  {key}: {quote(val)}")
            existing[key] = quote(val)
            changes.append(f"{key}: → metadata.{key}")
    for key in existing:  # spec: metadata values are strings — quote a bare one written by hand
        for i, ln in enumerate(meta_lines):
            sm = fmt.SUBKEY.match(ln)
            if sm and sm.group(1) == key and not fmt.is_quoted_string(fmt.strip_comment(sm.group(2) or "")):
                raw = sm.group(2) or ""
                bare = fmt.strip_comment(raw)
                comment = raw[len(bare):].strip()  # a trailing `# …` is kept, after the quoted value
                meta_lines[i] = f"  {key}: {quote(bare)}" + (f"  {comment}" if comment else "")
                changes.append(f"metadata.{key}: quoted")

    # 4. compatibility for every unit that requires something
    requires = fmt.parse_csv(existing.get("requires", ""))
    if requires and "compatibility" not in top:
        top["compatibility"] = [f"compatibility: {compatibility_for(requires)}"]
        changes.append("compatibility: added")
    if not changes:
        return text, []

    # 5. rebuild: identity keys first, metadata, then everything else in its original order
    out: list[str] = []
    for key in HEAD:
        if key in top:
            out += top[key]
    if meta_lines:
        out.append("metadata:")
        out += meta_lines
    for key, raw in entries:
        if key is None:
            out += raw
        elif key in HEAD or key in MOVED or key == "metadata":
            continue
        else:
            out += raw
    return "---\n" + "\n".join(out) + "\n---" + ("\n" + body if body or text.endswith("\n") else ""), changes


def migrate_file(p: Path, write: bool) -> list[str]:
    text = p.read_text(encoding="utf-8")
    new, changes = migrate_text(text)
    if changes and write:
        p.write_text(new, encoding="utf-8")
    return changes


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="migrate_frontmatter.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path, help="files to migrate (default: every skill and agent of the kit)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the pending changes, write nothing")
    mode.add_argument("--check", action="store_true", help="like --dry-run, exit 3 when anything is pending")
    a = ap.parse_args(argv[1:])
    paths = a.paths or fmt.units()
    pending = 0
    for p in paths:
        if not p.is_file():
            print(f"{p}: not a file", file=sys.stderr)
            return 2
        changes = migrate_file(p, write=not (a.dry_run or a.check))
        rel = p.resolve().relative_to(KIT) if p.resolve().is_relative_to(KIT) else p
        if changes:
            pending += 1
            verb = "would migrate" if (a.dry_run or a.check) else "migrated"
            print(f"{verb} {rel}: {'; '.join(changes)}")
        else:
            print(f"unchanged {rel}")
    print(f"{pending} of {len(paths)} file(s) {'pending' if (a.dry_run or a.check) else 'rewritten'}")
    return 3 if a.check and pending else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
