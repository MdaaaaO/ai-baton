#!/usr/bin/env python3
"""commit_style.py — which commit-message convention a repo follows, and does a message meet it.

Default (owner decision, 2026-09-25): **Conventional Commits** unless the repo overrides it.

Resolution order (first hit wins):
  1. the repo's own marker — `<repo>/.claude/commit-style` (one word: conventional | ticket-key | free)
     or a commitlint / conventional-release config in the repo root (→ conventional)
  2. the env store config `commits.repos["<owner/repo>"]`
  3. the env store config `commits.default`
  4. `conventional`

Styles:
  conventional  `<type>(<scope>)!: <description>` — type ∈ TYPES, optional lowercase scope, optional `!`,
                subject ≤ 72 chars, no trailing period. A tracker key, when a ticket applies, goes inside
                the description (`feat(dbt): KEY-123 add …`), never as a prefix.
  ticket-key    `<KEY>: <description>` — the key matches the env config's `tracker.key_regex`.
  free          anything non-empty.
`Merge …`, `Revert "…"` and `fixup!/squash! …` subjects always pass (git writes them).

`check` also refuses a "Generated with [Claude Code]" line or a `Co-Authored-By: Claude …` trailer anywhere in the
message (owner decision, 2026-09-28: no AI attribution anywhere — the `session `<name>`` self-identifier from
`kit_profile.py footer` is the only thing that stays), same as a bad subject.

Usage:
  commit_style.py resolve  [--dir <repo-dir>] [--repo <owner/repo>]
  commit_style.py check    [--dir …] [--repo …] [--style <s>] <msg-file | ->      exit 0 ok / 1 style / 2 usage, config or I/O
  commit_style.py title    [--dir …] [--repo …] [--style <s>] "<subject>"          same, for a PR title
  commit_style.py label    "<subject>"      → the GitHub type label a conventional subject implies (or "")
  commit_style.py types                     → the accepted types, one per line
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import kit_profile  # noqa: E402
except Exception:  # pragma: no cover — the script must work outside a workspace too
    kit_profile = None

STYLES = ("conventional", "ticket-key", "free")
TYPES = ("feat", "fix", "docs", "chore", "refactor", "test", "ci", "build", "perf", "style", "revert")
TYPE_LABEL = {"feat": "enhancement", "fix": "bug", "docs": "documentation"}
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9._/-]+)\))?(?P<bang>!)?: (?P<desc>\S.*)$")
ALWAYS_OK = re.compile(r'^(Merge |Revert "|fixup! |squash! )')
MAX_SUBJECT = 72
COMMITLINT = ("commitlint.config.js", "commitlint.config.cjs", "commitlint.config.mjs", "commitlint.config.ts",
              ".commitlintrc", ".commitlintrc.json", ".commitlintrc.yml", ".commitlintrc.yaml", ".commitlintrc.js")
CREL_TABLE = re.compile(r"^\[\[?tool\.conventional-release[\].]", re.M)
# no AI attribution anywhere (owner decision, 2026-09-28): the session self-identifier from `kit_profile.py footer`
# (`session `<name>``) is the only thing that stays — a "Generated with" line or a `Co-Authored-By: Claude …`
# trailer is refused on every commit, same as a bad subject. Matched narrowly — bare "Claude" or "Claude <model
# family>" immediately followed by the trailer's `<email>` — so a human co-author whose first name happens to be
# Claude (a "Claude <Surname> <email>" shape) is never caught.
ATTRIBUTION_RE = re.compile(
    r"Generated with \[Claude Code\]"
    r"|Co-Authored-By:\s*Claude(?:\s+(?:Code|Opus|Sonnet|Haiku|Fable)(?:[\s-]?\d+(?:\.\d+)*)?)?\s*<[^>\n]*>"
    r"|Co-Authored-By:[^\n<]*<noreply@anthropic\.com>",  # the vendor bot address, any model name; never a human there
    re.I,
)


def _config() -> dict:
    """The env store config, `{}` outside a workspace (no store: the kit default applies). A store that exists but
    cannot be read is NOT `{}`: its SystemExit propagates, and main() reports it as exit 2 — a broken `commits`
    override must never silently turn into "default style" and refuse every commit with a style hint."""
    if kit_profile is None or not kit_profile.env_config():
        return {}
    return kit_profile.load() or {}


def repo_slug(d: Path) -> str:
    """`owner/repo` from the origin remote, or "" when there is none."""
    try:
        url = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else ""


def repo_root(d: Path) -> Path:
    try:
        top = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
        return Path(top) if top else d
    except (subprocess.CalledProcessError, FileNotFoundError):
        return d


def resolve(d: Path | None = None, repo: str = "") -> tuple[str, str]:
    """→ (style, where-it-came-from)."""
    if d is not None:
        root = repo_root(d)
        marker = root / ".claude" / "commit-style"
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
            s = raw.split()[0].lower() if raw else ""
            if s in STYLES:
                return s, f"{marker} (repo marker)"
            raise SystemExit(f"{marker}: unknown style '{s}' (one of {', '.join(STYLES)})")
        if any((root / c).is_file() for c in COMMITLINT):
            return "conventional", "commitlint config in the repo root"
        if (root / ".conventional-release.toml").is_file():
            return "conventional", "conventional-release config in the repo root"
        pyproject = root / "pyproject.toml"
        # a table header, not a toml parse: tomllib is 3.11+ and the kit runs on older hosts too
        if pyproject.is_file() and CREL_TABLE.search(pyproject.read_text(encoding="utf-8", errors="replace")):
            return "conventional", "pyproject.toml `[tool.conventional-release]`"
        pkg = root / "package.json"
        if pkg.is_file():
            try:
                if "commitlint" in json.loads(pkg.read_text(encoding="utf-8")):
                    return "conventional", "package.json `commitlint` key"
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        repo = repo or repo_slug(root)
    cfg = _config().get("commits") or {}
    repos = cfg.get("repos") or {}
    if repo and repo in repos:
        s = str(repos[repo]).strip().lower()
        if s not in STYLES:
            raise SystemExit(f"env config commits.repos['{repo}'] = '{s}' is not one of {', '.join(STYLES)}")
        return s, f"env config commits.repos[{repo}]"
    default = str(cfg.get("default") or "conventional").strip().lower()
    if default not in STYLES:
        raise SystemExit(f"env config commits.default = '{default}' is not one of {', '.join(STYLES)}")
    return default, "env config commits.default" if cfg.get("default") else "kit default"


def key_regex() -> str:
    return str((_config().get("tracker") or {}).get("key_regex") or "")


def check_subject(subject: str, style: str) -> list[str]:
    """Problems with a subject line under `style` (empty list = ok)."""
    subject = subject.rstrip("\n")
    if not subject.strip():
        return ["empty subject"]
    if ALWAYS_OK.match(subject):
        return []
    problems: list[str] = []
    if style == "free":
        return problems
    if len(subject) > MAX_SUBJECT:
        problems.append(f"subject is {len(subject)} chars (max {MAX_SUBJECT})")
    if subject.rstrip().endswith("."):
        problems.append("subject ends with a period")
    if style == "conventional":
        m = CONVENTIONAL.match(subject)
        if not m:
            problems.append("not `<type>(<scope>)!: <description>` — e.g. `feat(dbt): KEY-123 add the fact table`")
        else:
            if m.group("type") not in TYPES:
                problems.append(f"type `{m.group('type')}` is not one of {', '.join(TYPES)}")
            desc = m.group("desc")
            if desc[:1].isupper() and not re.match(r"^[A-Z][A-Z0-9]*-\d+\b|^[A-Z]{2,}\b", desc):
                problems.append("description starts with a capital letter (lowercase, imperative: `add …`, not `Add …`)")
            rx = key_regex()
            if rx:
                try:
                    if re.match(rf"^(?:{rx})\s*[:\-]", subject):
                        problems.append("ticket key used as the prefix — put it inside the description: `feat(scope): KEY-123 …`")
                except re.error:
                    pass
    elif style == "ticket-key":
        rx = key_regex()
        if not rx:
            problems.append("style ticket-key but the env config has no tracker.key_regex")
        else:
            try:
                if not re.match(rf"^(?:{rx}):\s+\S", subject):
                    problems.append("not `<KEY>: <description>` (key per tracker.key_regex)")
            except re.error as e:
                problems.append(f"tracker.key_regex is not a valid regex: {e}")
    return problems


def comment_char(d: Path | None) -> str:
    """git's core.commentChar for the repo (default `#`) — lines starting with it are stripped."""
    if d is None:
        return "#"
    try:
        out = subprocess.run(["git", "-C", str(d), "config", "core.commentChar"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        out = ""
    return out if out and out != "auto" else "#"


def check_message(text: str, style: str, comment: str = "#") -> list[str]:
    # exactly git's rule (`cleanup=strip`, any editor-driven commit): EVERY line starting with the comment char goes, so
    # a `#123 fix` subject would be stripped by git too — a repo that wants `#`-first subjects sets core.commentChar
    lines = [ln for ln in text.splitlines() if not ln.startswith(comment)]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["empty message"]
    problems = check_subject(lines[0], style)
    if len(lines) > 1 and lines[1].strip():
        problems.append("second line must be blank (subject / blank / body)")
    m = ATTRIBUTION_RE.search("\n".join(lines))
    if m:
        problems.append(f"AI attribution (`{m.group(0)}`) — no attribution anywhere; the session self-identifier "
                         f"from `kit_profile.py footer` is the only thing that stays")
    return problems


def label_for(subject: str) -> str:
    m = CONVENTIONAL.match(subject.strip())
    return TYPE_LABEL.get(m.group("type"), "") if m else ""


def main(argv: list[str]) -> int:
    """0 ok · 1 the message / title breaks the style · 2 a usage, config or I/O error (the hook tells them apart)."""
    try:
        return _main(argv)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(f"commit_style: {e.code}", file=sys.stderr)
            return 2
        raise
    except OSError as e:
        print(f"commit_style: {e}", file=sys.stderr)
        return 2


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("resolve", "check", "title", "label", "types"))
    ap.add_argument("arg", nargs="?", help="message file (`-` = stdin) for check; subject for title/label")
    ap.add_argument("--dir", help="repo directory (marker / commitlint / origin lookup)")
    ap.add_argument("--repo", default="", help="owner/repo when --dir is not a clone")
    ap.add_argument("--style", choices=STYLES, help="skip resolution and check against this style")
    ap.add_argument("--quiet", "-q", action="store_true")
    a = ap.parse_intermixed_args(argv)  # py<3.12 parse_args drops `arg` when options sit between it and `cmd`
    if a.cmd == "types":
        print("\n".join(TYPES))
        return 0
    if a.cmd == "label":
        if a.arg is None:
            ap.error("label needs a subject")
        print(label_for(a.arg))
        return 0
    d = Path(a.dir).resolve() if a.dir else None
    if a.style:
        style, src = a.style, "--style"
    else:
        style, src = resolve(d, a.repo)
    if a.cmd == "resolve":
        print(style if a.quiet else f"{style}\t{src}")
        return 0
    if a.arg is None:
        ap.error(f"{a.cmd} needs a {'message file' if a.cmd == 'check' else 'subject'}")
    if a.cmd == "check":
        text = sys.stdin.read() if a.arg == "-" else Path(a.arg).read_text(encoding="utf-8", errors="replace")
        problems = check_message(text, style, comment_char(d))
        what = "commit message"
    else:
        problems = check_subject(a.arg, style)
        what = "PR title"
    if problems:
        print(f"{what} does not follow the {style} style ({src}):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        if style == "conventional":
            print(f"  types: {', '.join(TYPES)} · form: type(scope): description · repo override: .claude/commit-style", file=sys.stderr)
        return 1
    if not a.quiet:
        print(f"ok · {style} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
