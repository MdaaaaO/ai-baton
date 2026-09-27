#!/usr/bin/env python3
"""frontmatter.py — the one parser for a kit unit's YAML frontmatter (skills/*/SKILL.md, agents/*.md).

The kit follows the Agent Skills spec's "Claude Code profile": spec keys at the top level (`name`,
`description`, `license`, `compatibility`, `metadata`, `allowed-tools`) plus the Claude Code native keys
the kit relies on (docs/contributing.md § Skill frontmatter), and the kit's own bookkeeping — `version`,
`updated`, `reviewed`, `requires`, `facts` — under `metadata:` as string values:

    ---
    name: pr-open
    description: …
    compatibility: "Designed for Claude Code; needs slack (systems.*)"
    metadata:
      version: "6"
      updated: "2026-09-26"
      reviewed: "2026-09-25"
      requires: "slack"                       # comma-separated capability flags
      facts: "slack.channel eng-help,tracker.kind"   # comma-separated entries; space = kind + name
    user-invocable: true
    ---

No YAML library (stdlib only), so the dialect is deliberately small: `key: value` scalars at column 0,
one nested mapping level (`key:` with nothing after it, then two-space-indented `sub: value` lines), and
`#` comments outside quotes. Values are returned RAW (quotes kept) so a checker can see how they were
written; `unquote()` gives the plain string, `parse_csv()` the entries of a comma list (it also reads
the pre-2026-09-26 `[a, b]` form so a half-migrated tree still parses).

CLI (used by the env-init skill):  python3 frontmatter.py facts   → every declared `facts` entry, sorted, unique
                                   python3 frontmatter.py <file>  → the parsed frontmatter as JSON
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$")     # `key: value` / `key:` at column 0
SUBKEY = re.compile(r"^  ([A-Za-z_][\w-]*):(?:\s+(.*))?$")  # two-space-indented `sub: value`
# the kit's bookkeeping keys — under `metadata:` since 2026-09-26; rejected at the top level
KIT_META_KEYS = ("version", "updated", "reviewed", "requires", "facts")

Frontmatter = dict[str, "str | dict[str, str]"]


def units(kit: Path = KIT) -> list[Path]:
    """Every skill and agent file, sorted (skills first). Claude Code loads every `skills/*/SKILL.md`, so every one of
    them is a unit — the skill scaffold lives outside the loaded tree (`docs/templates/skill/`)."""
    return sorted(kit.glob("skills/*/SKILL.md")) + sorted(kit.glob("agents/*.md"))


def unit_name(p: Path) -> str:
    """`pr-open` for skills/pr-open/SKILL.md, `triage` for agents/triage.md."""
    return p.parent.name if p.name == "SKILL.md" else p.stem


def split(text: str) -> tuple[list[str], str] | None:
    """(frontmatter lines without the fences, body after the closing fence) — None without a block."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None  # unterminated block


def strip_comment(v: str) -> str:
    """Drop a trailing ` # comment` that is outside quotes."""
    v = v.strip()
    if v and v[0] in "\"'":
        q = v[0]
        end = v.find(q, 1)
        return v if end < 0 else v[:end + 1]
    m = re.search(r"\s+#", v)
    return v[:m.start()].rstrip() if m else v


def parse_lines(lines: list[str]) -> Frontmatter:
    fm: Frontmatter = {}
    current: str | None = None  # the key whose nested mapping we are inside
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = KEY.match(line)
        if m:
            key, val = m.group(1), m.group(2)
            if val is None or not strip_comment(val):
                fm[key] = {}
                current = key
            else:
                fm[key] = strip_comment(val)
                current = None
            continue
        sm = SUBKEY.match(line)
        if sm and current is not None and isinstance(fm.get(current), dict):
            fm[current][sm.group(1)] = strip_comment(sm.group(2) or "")  # type: ignore[index]
            continue
        # anything else (a continuation line, a deeper nesting) is kept out — the checker reports it
        fm.setdefault("_unparsed", {})[str(len(fm.get("_unparsed", {})))] = line  # type: ignore[index]
    return fm


def duplicate_keys(lines: list[str]) -> list[str]:
    """Keys a block repeats at the same level, as `key` / `parent.key` with both line numbers (1-based within the
    block) — the parser keeps the last value silently, so a mis-resolved merge that leaves two `version:` lines under
    `metadata:` would otherwise pass (#19)."""
    seen: dict[str, int] = {}
    out: list[str] = []
    current: str | None = None
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = KEY.match(line)
        if m:
            name = m.group(1)
            val = m.group(2)
            current = name if val is None or not strip_comment(val) else None
        else:
            sm = SUBKEY.match(line)
            if not (sm and current is not None):
                continue
            name = f"{current}.{sm.group(1)}"
        if name in seen:
            out.append(f"`{name}` on lines {seen[name]} and {n}")
        else:
            seen[name] = n
    return out


def parse_flat(lines: list[str]) -> dict[str, str]:
    """The flat `key: value` reading a `.context/` document gets (gen_index, verify, session.py): every line with a
    colon is a column, split at the first colon, both sides stripped, values verbatim (a quoted or bracketed value
    is the caller's to interpret — `norm_tags`). Lines without a colon are ignored."""
    meta: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip()
    return meta


def load_flat(path: Path, errors: str = "strict") -> dict[str, str] | None:
    """`parse_flat` of a file's frontmatter; None without a block."""
    parts = split(path.read_text(encoding="utf-8", errors=errors))
    return None if parts is None else parse_flat(parts[0])


def parse(text: str) -> Frontmatter | None:
    """The frontmatter mapping (values raw), or None when the text has no `---` block."""
    parts = split(text)
    return None if parts is None else parse_lines(parts[0])


def load(path: Path) -> Frontmatter | None:
    return parse(path.read_text(encoding="utf-8", errors="replace"))


def unquote(v) -> str:
    """The plain string behind a raw value: matching surrounding quotes removed, else as is."""
    if not isinstance(v, str):
        return ""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def is_quoted_string(v) -> bool:
    """True when the raw value is written as a double-quoted string — the spec's `metadata` is string→string."""
    return isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"'


def parse_csv(v) -> list[str]:
    """Entries of a comma-separated string value; also reads the legacy inline list `[a, b]`."""
    s = unquote(v)
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [x.strip().strip("'\"") for x in s.split(",") if x.strip()]


def meta(fm: Frontmatter | None) -> dict[str, str]:
    """The unit's `metadata:` mapping (raw values), `{}` when absent or not a mapping."""
    if not fm:
        return {}
    m = fm.get("metadata")
    return dict(m) if isinstance(m, dict) else {}


def facts_of(fm: Frontmatter | None) -> list[str]:
    return parse_csv(meta(fm).get("facts", ""))


def requires_of(fm: Frontmatter | None) -> list[str]:
    return parse_csv(meta(fm).get("requires", ""))


def all_facts(kit: Path = KIT) -> list[str]:
    """Every `facts` entry declared by an installed skill or agent, sorted and unique."""
    out: set[str] = set()
    for p in units(kit):
        out.update(facts_of(load(p)))
    return sorted(out)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: frontmatter.py facts | frontmatter.py <file>", file=sys.stderr)
        return 2
    if argv[1] == "facts":
        print("\n".join(all_facts()))
        return 0
    fm = load(Path(argv[1]))
    if fm is None:
        print(f"{argv[1]}: no frontmatter block", file=sys.stderr)
        return 1
    print(json.dumps(fm, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
