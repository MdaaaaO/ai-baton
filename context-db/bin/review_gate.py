#!/usr/bin/env python3
"""review_gate.py — the review's tier 0: what a diff against the base branch decides without a model.

Two checks, both diff-based, both failing the PR in `ci.yml` (`make -C $BATON/context-db review-gate BASE=origin/main`
locally):

  leak   every line the PR ADDS is scanned with the shared leak shapes (`leak_shapes.LEAK_SHAPES`: chat/user ids,
         ticket keys, account ids, org hosts, timezone literals, …) plus the PII shapes only a diff scan needs:
         e-mail addresses, home paths (`/home/<x>`, `/Users/<x>`), token shapes (GitHub, Slack, AWS, API keys, private
         keys). Pre-existing lines are not findings (the reviewer's rule), `skills/kit-health/allow.txt` is honoured, and
         the scanners' own files are skipped. A login or a person's name has no shape — that stays with kit-health's
         identity scan (this machine's values) and the reviewer's leak-by-meaning lens.
  bump   a skill or agent with a changed file (anything under `skills/<x>/` except README.md, or `agents/<x>.md`) bumps
         `metadata.version` (a higher integer than base), sets `metadata.updated` to a later date, and gets a new
         `docs/CHANGELOG.md` line that names it; a new unit needs the CHANGELOG line; a deleted unit too. Wording-only
         edits are exempt with `--skip-bump` (ci.yml passes it for the `wording` label or `[skip-bump]` in the PR
         title/body — the exemption is the author's explicit claim, visible on the PR).

Every finding is one line in the review's fixed shape: `[STOP] path:line — claim (rule)` (docs/REVIEW.md § Findings).
Exit 1 on any finding, 0 with a one-line summary otherwise; `--json` prints the findings as a list for
review_evidence.py. Stdlib only; needs `git` and a checkout that has the base ref (`fetch-depth: 0`).
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import frontmatter as fmt  # noqa: E402
import leak_shapes  # noqa: E402

KIT = HERE.parents[1]
CHANGELOG = "docs/CHANGELOG.md"
RULE_LEAK = "REVIEW.md § 2.1"
RULE_BUMP = "docs/contributing.md § Versioning"

# Shapes a diff scan adds to the shared list: they are PII/secret shapes, not environment facts, so kit-health's
# every-file scan (which has this machine's real values) does not need them, and a diff scan has nothing else.
PII_SHAPES = [
    (r"(?<![\w.+-])(?!git@)[\w.+-]+@(?!example\.(?:com|org|net)\b)(?!users\.noreply\.github\.com\b)(?!noreply\.github\.com\b)[\w-]+(?:\.[\w-]+)+(?![\w-])", "e-mail address"),
    (r"(?<![\w-])/(?:home|Users)/(?!<)(?!user\b)(?!runner\b)(?!\$)[A-Za-z][\w.-]*(?=[/\s`'\")]|$)", "home path (use `~`, `$HOME` or `<user>`)"),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "GitHub fine-grained token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b", "API key"),
    (r"\bglpat-[A-Za-z0-9_-]{20,}\b", "GitLab token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
]
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico", ".woff", ".woff2")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def repo_root() -> Path:
    """The repository the gate runs in: the git top level of the working directory when that is a kit checkout (CI runs
    from the kit; the tests from a throw-away kit-shaped repo), else this kit — never an unrelated repo the caller
    happens to stand in (a workspace root that is its own git repo)."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    top = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if top and ((top / "context-db" / "bin" / "review_gate.py").is_file() or (top / "skills").is_dir() and (top / "docs").is_dir()):
        return top
    return KIT


def git(*args: str, cwd: Path = KIT) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"review_gate: git {' '.join(args)} failed — {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def merge_base(base: str, head: str, cwd: Path = KIT) -> str:
    return git("merge-base", base, head, cwd=cwd).strip()


def changed_files(base: str, head: str, cwd: Path = KIT) -> dict[str, str]:
    """{path: status} for the PR's own files (status A/M/D/R…), from the merge base so a base merge is not the PR's."""
    mb = merge_base(base, head, cwd)
    out: dict[str, str] = {}
    for line in git("diff", "--name-status", "-M", mb, head, cwd=cwd).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0][0], parts[-1]
        out[path] = status
    return out


def added_lines(base: str, head: str, path: str, cwd: Path = KIT) -> list[tuple[int, str]]:
    """(new line number, text) for every line the PR adds to `path`."""
    mb = merge_base(base, head, cwd)
    out: list[tuple[int, str]] = []
    n = 0
    for line in git("diff", "-U0", "--no-color", mb, head, "--", path, cwd=cwd).splitlines():
        m = HUNK.match(line)
        if m:
            n = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append((n, line[1:]))
            n += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif not line.startswith("\\"):
            n += 1
    return out


def leak_findings(base: str, head: str, cwd: Path = KIT, files: dict[str, str] | None = None) -> list[str]:
    files = changed_files(base, head, cwd) if files is None else files
    shapes = leak_shapes.shapes() + [(re.compile(rx), what) for rx, what in PII_SHAPES]
    allow = leak_shapes.allowed(cwd / "skills" / "kit-health" / "allow.txt")  # the repo under review's own allow-list
    out: list[str] = []
    for path, status in sorted(files.items()):
        if status == "D" or Path(path).name in leak_shapes.SKIP_FILES or path.lower().endswith(SKIP_SUFFIXES):
            continue
        if path == "context-db/bin/review_gate.py":  # this file names the shapes it scans for
            continue
        for n, text in added_lines(base, head, path, cwd):
            hits = leak_shapes.scan(text, shapes=shapes, rel=path, allow=allow)
            for _one, what, hit in hits:
                out.append(f"[STOP] {path}:{n} — {what} `{hit}` added; an environment's or a person's value never ships with the kit ({RULE_LEAK})")
    return out


def unit_of(path: str) -> str | None:
    """The unit a changed path belongs to (`skills/<x>/SKILL.md` or `agents/<x>.md`), None for engine/docs/CI files."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "skills":
        return None if parts[-1] == "README.md" else f"skills/{parts[1]}/SKILL.md"
    if len(parts) == 2 and parts[0] == "agents" and path.endswith(".md"):
        return path
    return None


def unit_name(unit: str) -> str:
    return unit.split("/")[1] if unit.startswith("skills/") else Path(unit).stem


def show(ref: str, path: str, cwd: Path = KIT) -> str | None:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def meta(text: str | None) -> tuple[int | None, str]:
    """(metadata.version as int, metadata.updated) from a unit's text; (None, "") when absent or unparsable."""
    fm = fmt.parse(text) if text else None
    md = (fm or {}).get("metadata") if fm else None
    md = md if isinstance(md, dict) else {}
    v = fmt.unquote(md.get("version", ""))
    try:
        vi: int | None = int(v)
    except (TypeError, ValueError):
        vi = None
    return vi, fmt.unquote(md.get("updated", ""))


def bump_findings(base: str, head: str, cwd: Path = KIT, files: dict[str, str] | None = None) -> list[str]:
    files = changed_files(base, head, cwd) if files is None else files
    mb = merge_base(base, head, cwd)
    changelog_added = "\n".join(t for _n, t in added_lines(base, head, CHANGELOG, cwd)) if CHANGELOG in files else ""
    units = sorted({u for u in (unit_of(p) for p in files) if u})
    out: list[str] = []
    for unit in units:
        name = unit_name(unit)
        before, after = show(mb, unit, cwd), show(head, unit, cwd)
        mentioned = re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", changelog_added) is not None
        if after is None:  # deleted unit
            if not mentioned:
                out.append(f"[STOP] {CHANGELOG}:1 — `{name}` is removed but no added CHANGELOG line names it ({RULE_BUMP})")
            continue
        v_new, u_new = meta(after)
        if before is None:  # new unit
            if v_new is None:
                out.append(f"[STOP] {unit}:1 — new unit without an integer `metadata.version` ({RULE_BUMP})")
            if not mentioned:
                out.append(f"[STOP] {CHANGELOG}:1 — new unit `{name}` has no CHANGELOG line ({RULE_BUMP})")
            continue
        v_old, u_old = meta(before)
        if v_new is None or v_old is None or v_new <= v_old:
            out.append(f"[STOP] {unit}:1 — files of `{name}` changed but `metadata.version` is {v_new!r} (base {v_old!r}); bump it, or mark the PR "
                       f"wording-only (`wording` label / `[skip-bump]`) ({RULE_BUMP})")
        if not u_new or (u_old and u_new < u_old):  # same day as the base's last bump is fine (two PRs in one day)
            out.append(f"[STOP] {unit}:1 — `metadata.updated` is {u_new!r} (base {u_old!r}); set it to the change's date ({RULE_BUMP})")
        elif u_new > date.today().isoformat():
            out.append(f"[STOP] {unit}:1 — `metadata.updated` {u_new!r} is in the future ({RULE_BUMP})")
        if not mentioned:
            out.append(f"[STOP] {CHANGELOG}:1 — `{name}` changed but no added CHANGELOG line names it ({RULE_BUMP})")
    return out


def run(base: str, head: str = "HEAD", skip_bump: bool = False, cwd: Path = KIT) -> tuple[list[str], dict[str, str]]:
    files = changed_files(base, head, cwd)
    findings = leak_findings(base, head, cwd, files)
    if not skip_bump:
        findings += bump_findings(base, head, cwd, files)
    return findings, files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tier-0 review gate: leak shapes on added lines, version/CHANGELOG bumps")
    ap.add_argument("check", nargs="?", choices=("all", "leak", "bump"), default="all")
    ap.add_argument("--base", default="origin/main", help="the base ref (default origin/main)")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--skip-bump", action="store_true", help="wording-only PR: no version/CHANGELOG check")
    ap.add_argument("--json", action="store_true", help="print the findings as a JSON list")
    ap.add_argument("--repo", type=Path, default=None, help="the repository (default: the working directory's git top level)")
    a = ap.parse_args(argv)
    cwd = a.repo or repo_root()
    files = changed_files(a.base, a.head, cwd)
    findings: list[str] = []
    if a.check in ("all", "leak"):
        findings += leak_findings(a.base, a.head, cwd, files)
    if a.check in ("all", "bump") and not a.skip_bump:
        findings += bump_findings(a.base, a.head, cwd, files)
    if a.json:
        print(json.dumps(findings))
    else:
        for f in findings:
            print(f)
        units = sorted({unit_name(u) for u in (unit_of(p) for p in files) if u})
        print(f"review-gate: {'FAIL' if findings else 'OK'} — {len(files)} changed file(s), {len(units)} unit(s){' [' + ', '.join(units) + ']' if units else ''}"
              f"{', bump check skipped (wording-only)' if a.skip_bump else ''}, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
