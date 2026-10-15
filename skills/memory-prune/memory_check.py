#!/usr/bin/env python3
"""memory_check.py — the read-only audit behind the `memory-prune` skill: is the harness auto-memory cheap, consistent
and free of copies of the always-on prefix?

    python3 $BATON/skills/memory-prune/memory_check.py [--dir <memory dir>] [--workspace <WORKSPACE.md>] [--dump]

The memory dir is the one `setup.sh` wires: `--dir`, else `MEMORY_DIR`, else `<content root>/memory` (the durable
copy), else `~/.claude/projects/<slug>/memory` (the harness dir the symlink points from). `MEMORY.md` is the index;
every other `*.md` in the dir is a note.

Prints one summary line, then one line per finding, `<KIND> <file>[:<line>] <detail>`:
  INDEX_BUDGET   the index is over `MEMORY_INDEX_WARN` bytes (default 12288)
  LONG_LINE      an index line is over `MEMORY_LINE_WARN` characters (default 150)
  NO_FILE        an index line links a note that does not exist
  NO_INDEX       a note no index line links
  DANGLING       a `[[link]]` names no note in the dir
  FRONTMATTER    a note without a valid `name` / `description` / `metadata.type` block
  RULES_QUOTE    a note body repeats a WORKSPACE.md § Rules line verbatim (>= 12 consecutive words)
  SECRET         a secret-shaped string (`leak_shapes.SECRET_SHAPES`) — the kind and line only, never the value
  LARGE          a note over 10 KB (the classifier judges it per section)

`--dump` prints the archive text instead: a `## Run <date time>` heading, then every note and `MEMORY.md` verbatim,
each under `### <file>` in its own fence, secret-shaped values replaced by `<redacted: <kind>>` (a private-key block
whole, BEGIN to END) — the input
`memory-prune` parks in `archive/` before it changes anything. Exit 0, or 2 on a missing dir.

Exit 0 within budget · 1 past the budget (INDEX_BUDGET or LONG_LINE) or on any SECRET (the summary names which) · 2 when
the memory dir is missing. Never writes.
Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT / "context-db" / "bin"))
import frontmatter as fmt  # noqa: E402
import leak_shapes  # noqa: E402

INDEX = "MEMORY.md"
INDEX_WARN = 12288
LINE_WARN = 150
LARGE_NOTE = 10 * 1024
QUOTE_WORDS = 12
NOTE_TYPES = ("user", "feedback", "project", "reference")
HIST_EDGES = (80, 150, 200)  # index line-length histogram buckets: <=80 · 81-150 · 151-200 · >200

LINK_RE = re.compile(r"\]\(([^)\s]+?\.md)(?:#[^)]*)?\)")
WIKI_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
SECRETS = [(re.compile(rx), what) for rx, what in leak_shapes.SECRET_SHAPES]
# The multi-line secrets the shapes above only see by their first line: a PEM/OpenSSH/PGP private-key block, from its
# BEGIN line through the matching END line (or the end of the text when the END line is missing), so `--dump` keeps
# neither the key body nor the END line. Applied before the one-line shapes.
SECRET_BLOCKS = [(re.compile(r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?)-----.*?(?:-----END \1-----|\Z)", re.S),
                  "private key")]


def env_int(raw: str, default: int) -> int:
    """A knob's value as an int; empty or not a number → the default."""
    raw = raw.strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def memory_dir(arg: str | None = None) -> Path:
    """The memory dir as `setup.sh` wires it: the argument, `MEMORY_DIR`, the durable `<content root>/memory`, else the
    harness dir `~/.claude/projects/<slug>/memory`. A path that does not exist is returned as is (the caller exits 2)."""
    if arg:
        return Path(arg)
    env = os.environ.get("MEMORY_DIR", "").strip()
    if env:
        return Path(env)
    try:
        import kit_profile  # the one content-root resolver; imported late so a broken store still gets a path
        root = kit_profile.context_root()
        slug = kit_profile.harness_project_slug(str(root.parent))
    except (Exception, SystemExit):
        root = Path(os.environ.get("CONTEXT_ROOT", "").strip() or ".context")
        slug = re.sub("/", "-", str(root.resolve().parent))
    durable = root / "memory"
    harness = Path.home() / ".claude" / "projects" / slug / "memory"
    return durable if durable.is_dir() or not harness.is_dir() else harness


def words(text: str) -> list[str]:
    """Lower-cased word tokens with markdown punctuation dropped, so `**Never**` and `never` compare equal."""
    return WORD_RE.findall(text.lower().replace("`", "").replace("*", ""))


def rules_lines(workspace: Path) -> list[str]:
    """Each bullet of WORKSPACE.md § Rules (continuation lines joined) — the always-on rules a note must not repeat."""
    try:
        text = workspace.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    m = re.search(r"^## Rules\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return []
    out: list[str] = []
    for line in m.group(1).splitlines():
        if line.startswith("- "):
            out.append(line[2:].strip())
        elif line.startswith("  ") and out and line.strip():
            out[-1] += " " + line.strip()
    return out


def shingles(rules: list[str], n: int = QUOTE_WORDS) -> dict[tuple[str, ...], int]:
    """Every run of `n` consecutive words of every rule → the rule's index."""
    out: dict[tuple[str, ...], int] = {}
    for i, rule in enumerate(rules):
        w = words(rule)
        for j in range(len(w) - n + 1):
            out.setdefault(tuple(w[j:j + n]), i)
    return out


def frontmatter_problem(text: str) -> str | None:
    """Why a note's frontmatter is not valid, or None: a `---` block with `name`, `description` and a `type` of
    user/feedback/project/reference under `metadata:` (a top-level `type:` is the older harness form, also read)."""
    fm = fmt.parse(text)
    if fm is None:
        return "no `---` frontmatter block"
    missing = [k for k in ("name", "description") if not fmt.unquote(fm.get(k))]
    kind = fmt.unquote(fmt.meta(fm).get("type")) or fmt.unquote(fm.get("type"))
    if not kind:
        missing.append("metadata.type")
    if missing:
        return "missing " + ", ".join(missing)
    if kind not in NOTE_TYPES:
        return f"metadata.type `{kind}` is not one of {'/'.join(NOTE_TYPES)}"
    return None


def histogram(lengths: list[int]) -> str:
    counts = [0] * (len(HIST_EDGES) + 1)
    for n in lengths:
        counts[next((i for i, e in enumerate(HIST_EDGES) if n <= e), len(HIST_EDGES))] += 1
    labels = [f"<={HIST_EDGES[0]}"] + [f"{a + 1}-{b}" for a, b in zip(HIST_EDGES, HIST_EDGES[1:])] + [f">{HIST_EDGES[-1]}"]
    return " · ".join(f"{lab}: {c}" for lab, c in zip(labels, counts))


def secret_hits(name: str, text: str) -> list[str]:
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        for rx, what in SECRETS:
            if rx.search(line):
                out.append(f"SECRET {name}:{no} {what} — redact before any archive or rewrite")
    return out


def redact(text: str) -> str:
    """`text` with every secret-shaped value replaced by `<redacted: <kind>>` — the ctx store refuses the original. A
    multi-line block (`SECRET_BLOCKS`) goes whole, BEGIN to END, before the one-line shapes run."""
    for rx, what in SECRET_BLOCKS + SECRETS:
        text = rx.sub(f"<redacted: {what}>", text)
    return text


def dump(mem: Path, now: str) -> str:
    """The archive text: every note, then the index, verbatim apart from redaction, each in a fence longer than any
    backtick run inside it so a note's own headings and code blocks stay inside its section."""
    files = sorted(p for p in mem.glob("*.md") if p.name != INDEX and p.is_file())
    if (mem / INDEX).is_file():
        files.append(mem / INDEX)
    out = [f"## Run {now} — {len(files)} files", ""]
    for p in files:
        text = redact(p.read_text(encoding="utf-8", errors="replace")).rstrip("\n")
        longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
        fence = "`" * max(4, longest + 1)
        out += [f"### {p.name}", "", f"{fence}markdown", text, fence, ""]
    return "\n".join(out)


def check(mem: Path, workspace: Path, index_warn: int = INDEX_WARN, line_warn: int = LINE_WARN) -> tuple[str, list[str], bool]:
    """(summary line, findings, act now) for an existing memory dir: act now is past the budget or any SECRET."""
    findings: list[str] = []
    idx = mem / INDEX
    index_text = idx.read_text(encoding="utf-8", errors="replace") if idx.is_file() else ""
    index_bytes = len(index_text.encode("utf-8"))
    index_lines = index_text.splitlines()
    notes = sorted(p for p in mem.glob("*.md") if p.name != INDEX and p.is_file())
    names = {p.name for p in notes}
    stems = {p.stem for p in notes}

    over = False
    if not idx.is_file():
        findings.append(f"NO_FILE {INDEX} — the index itself is missing")
    if index_bytes > index_warn:
        over = True
        findings.append(f"INDEX_BUDGET {INDEX} {index_bytes} B > {index_warn} B")
    long_lines = 0
    linked: set[str] = set()
    for no, line in enumerate(index_lines, 1):
        if len(line) > line_warn:
            long_lines += 1
            over = True
            findings.append(f"LONG_LINE {INDEX}:{no} {len(line)} chars > {line_warn}")
        for target in LINK_RE.findall(line):
            base = Path(target).name
            linked.add(base)
            if base not in names:
                findings.append(f"NO_FILE {INDEX}:{no} links `{target}`, which is not in the memory dir")
    for p in notes:
        if p.name not in linked:
            findings.append(f"NO_INDEX {p.name} — no {INDEX} line links it")

    for no, line in enumerate(index_lines, 1):
        for target in WIKI_RE.findall(line):
            t = target.strip()
            if t not in stems and t not in names:
                findings.append(f"DANGLING {INDEX}:{no} [[{t}]]")
    findings += secret_hits(INDEX, index_text)

    rules = rules_lines(workspace)
    grams = shingles(rules)
    body_bytes = 0
    for p in notes:
        text = p.read_text(encoding="utf-8", errors="replace")
        size = len(text.encode("utf-8"))
        body_bytes += size
        problem = frontmatter_problem(text)
        if problem:
            findings.append(f"FRONTMATTER {p.name} {problem}")
        parts = fmt.split(text)
        body = parts[1] if parts else text
        for no, line in enumerate(text.splitlines(), 1):
            for target in WIKI_RE.findall(line):
                t = target.strip()
                if t not in stems and t not in names:
                    findings.append(f"DANGLING {p.name}:{no} [[{t}]]")
        w = words(body)
        quoted: list[int] = []
        for j in range(len(w) - QUOTE_WORDS + 1):
            i = grams.get(tuple(w[j:j + QUOTE_WORDS]))
            if i is not None and i not in quoted:
                quoted.append(i)
        for i in quoted:
            findings.append(f"RULES_QUOTE {p.name} repeats WORKSPACE.md § Rules: \"{' '.join(words(rules[i])[:8])} …\"")
        findings += secret_hits(p.name, text)
        if size > LARGE_NOTE:
            findings.append(f"LARGE {p.name} {size // 1024} KB — judge it per section")

    secrets = sum(1 for f in findings if f.startswith("SECRET "))
    flags = (["OVER BUDGET"] if over else []) + ([f"{secrets} SECRET"] if secrets else [])
    summary = (f"memory-check: index {index_bytes} B / {index_warn} B · {len(index_lines)} lines "
               f"({histogram([len(x) for x in index_lines])}) · {long_lines} over {line_warn} chars · "
               f"{len(notes)} notes {body_bytes // 1024} KB · {len(findings)} findings · "
               + (" + ".join(flags) + " — /memory-prune" if flags else "within budget"))
    return summary, findings, bool(flags)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only audit of the harness auto-memory dir.")
    ap.add_argument("--dir", help="the memory dir (default: MEMORY_DIR, else the dir setup.sh wires)")
    ap.add_argument("--dump", action="store_true", help="print every original, redacted and fenced, for the archive")
    ap.add_argument("--workspace", default=str(KIT / "WORKSPACE.md"), help="the WORKSPACE.md whose § Rules notes must not repeat")
    a = ap.parse_args(argv)
    mem = memory_dir(a.dir)
    if not mem.is_dir():
        print(f"memory-check: no memory dir at {mem} — nothing to audit (setup.sh creates it; MEMORY_DIR overrides)")
        return 2
    if a.dump:
        print(dump(mem, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
        return 0
    index_warn = env_int(os.environ.get("MEMORY_INDEX_WARN", ""), INDEX_WARN)
    line_warn = env_int(os.environ.get("MEMORY_LINE_WARN", ""), LINE_WARN)
    summary, findings, act = check(mem, Path(a.workspace), index_warn, line_warn)
    print(summary)
    for f in findings:
        print(f)
    return 1 if act else 0


if __name__ == "__main__":
    sys.exit(main())
