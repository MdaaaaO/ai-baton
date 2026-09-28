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
the pre-2026-09-26 `[a, b]` form so a half-migrated tree still parses). `plain_scalar_problem()` says why
a bare value would not be a valid YAML plain scalar (a real parser would refuse it even though this lenient
one reads it fine); `quote()` is the one double-quoting function every writer uses when it applies.

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


def _closing_quote(v: str, q: str) -> int:
    """Index of the closing `q` (`"` or `'`) that ends a quoted value starting at v[0], honoring the one escape
    each quote style uses inside a value this module's own `quote()` ever writes: `\\"` / `\\\\` inside a
    double-quoted value, `''` (a doubled quote) inside a single-quoted one. -1 when it never closes. A naive
    `v.find(q, 1)` stops at the first escaped quote instead — the value `"…with \\"SSO session\\"…"` was cut
    there, dropping everything after."""
    i, n = 1, len(v)
    while i < n:
        c = v[i]
        if q == '"' and c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == q:
            if q == "'" and i + 1 < n and v[i + 1] == "'":
                i += 2  # `''` inside a single-quoted value is one literal quote, not the close
                continue
            return i
        i += 1
    return -1


def strip_comment(v: str) -> str:
    """Drop a trailing ` # comment` that is outside quotes."""
    v = v.strip()
    if v and v[0] in "\"'":
        end = _closing_quote(v, v[0])
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
    """The plain string behind a raw value: matching surrounding quotes removed, else as is. The inverse of
    `quote()` for a double-quoted value: `\\"` and `\\\\` are unescaped back to `"` and `\\` — the only two
    escapes `quote()` ever writes — so a value round-tripped through quote()/unquote() (or written by hand
    the same way) reads identically to a real YAML parser, not just "the quotes come off"."""
    if not isinstance(v, str):
        return ""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        return re.sub(r'\\(["\\])', r"\1", inner) if v[0] == '"' else inner.replace("''", "'")  # YAML's one single-quote escape
    return v


def is_quoted_string(v) -> bool:
    """True when the raw value is written as a double-quoted string — the spec's `metadata` is string→string,
    so a hand-written single-quoted or bare metadata value must still be canonicalized to double quotes."""
    return isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"'


def is_quoted(v) -> bool:
    """True when the raw value already starts with a quote character, either style (`"` or `'`) — "already
    quoted, leave it alone" for a top-level scalar (unlike `is_quoted_string`, which only counts the spec's
    required double-quoted form for `metadata:`). The one check kit_verify.py and migrate_frontmatter.py both
    use so a valid single-quoted value (`description: 'Demo: skill'`) is recognized as quoted in both places,
    not flagged and rewritten by one of them."""
    return isinstance(v, str) and bool(v) and v[0] in "\"'"


# The plain-scalar shapes that actually recur in kit values and would make a REAL YAML parser (the ctx-store
# backend, PyYAML) refuse the file, even though this module's lenient line parser reads them fine: a
# description or a session field written as free text often contains one of these by accident. `[` and `{`
# are not here: a value that opens and closes one (`arguments: [repo, pr, event]`) is a deliberate YAML flow
# collection — valid as is, and this module's own `parse_csv` reads that exact legacy form — so those two are
# checked separately, only when the bracket is never closed (FLOW_OPEN below). `#` and `,` are always
# indicators wherever they lead (a comment start, a flow-context separator); `-`, `?` and `:` are indicators
# only in the specific shape YAML restricts (LEADING_WITH_SPACE below) — `-5` or `-quiet` is a fine plain
# scalar, `- ` (or a bare `-`) is a sequence-entry indicator.
PLAIN_SCALAR_INDICATORS = "]}&*!|>'\"%@`#,"
FLOW_OPEN = {"[": "]", "{": "}"}  # opens a flow sequence / mapping; fine when the value is a balanced one
LEADING_WITH_SPACE = "-?:"  # `- `, `? `, `: ` (or the bare character alone) are indicators; `-x`/`:x` are not


def plain_scalar_problem(v: str) -> str | None:
    """Why the bare (unquoted) value `v` would not be a valid YAML plain scalar, or None when it is fine
    unquoted — INCLUDING when it is not a plain scalar at all but a valid flow collection (`[a, b]`, `{a: b}`).
    Stdlib substitute for a real YAML parser: an unbalanced or plain indicator character, a leading `-`/`?`/`:`
    followed by a space (or alone), ': ' (read as a nested mapping), ' #' (read as a comment, wherever it
    falls) and a trailing ':' (read as an empty mapping). Covers a leading '#' and a ' #' anywhere so a caller
    never has to remember to check those separately (kit_verify.py still reports its own richer message for
    them — with the exact text YAML would keep — before it ever reaches this function; harmless overlap)."""
    if not v:
        return None
    close = FLOW_OPEN.get(v[0])
    if close is not None:
        return None if v.rstrip().endswith(close) else f"starts with '{v[0]}' with no matching '{close}'"
    if v[0] in LEADING_WITH_SPACE and (len(v) == 1 or v[1] == " "):
        return f"starts with '{v[0]}' followed by a space (or alone), a YAML indicator sequence"
    if v[0] in PLAIN_SCALAR_INDICATORS:
        return f"starts with '{v[0]}', a YAML indicator character"
    if ": " in v:
        return "contains ': ', which YAML reads as a nested mapping"
    if re.search(r"\s#", v):
        return "contains ' #', which YAML reads as a comment"
    if v.rstrip().endswith(":"):
        return "ends with ':', which YAML reads as an empty mapping"
    return None


def quote(v: str) -> str:
    """`v` (a bare, unquoted value) as a double-quoted YAML scalar: backslashes escaped before quotes (so an
    already-escaped quote is never double-escaped), wrapped in double quotes. The one quoting function every
    kit writer uses (migrate_frontmatter.py, session.py) so a value that needs quoting is quoted the same way
    everywhere."""
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
