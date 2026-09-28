#!/usr/bin/env python3
"""review_gate.py — the review's tier 0: what a diff against the base branch decides without a model.

Two checks, both diff-based, both failing the PR in `ci.yml` (`make -C $BATON/context-db review-gate BASE=origin/main`
locally):

  leak   every line the PR ADDS is scanned with the shared leak shapes (`leak_shapes.LEAK_SHAPES`: chat/user ids,
         ticket keys, account ids, org hosts, timezone literals, …) plus the PII shapes only a diff scan needs:
         e-mail addresses, home paths (`/home/<x>`, `/Users/<x>`), token shapes (GitHub, Slack, AWS, API keys, private
         keys). Pre-existing lines are not findings (the reviewer's rule), `skills/kit-health/allow.txt` AS THE BASE HAS IT is honoured (a PR never widens the list that judges it, #129), and
         the scanners' own files are skipped. A login or a person's name has no shape — that stays with kit-health's
         identity scan (this machine's values) and the reviewer's leak-by-meaning lens.
  bump   a skill or agent with a changed file (anything under `skills/<x>/` except README.md, or `agents/<x>.md`) bumps
         `metadata.version` (a higher integer than base) and sets `metadata.updated` to a later date; a new unit needs an
         integer version. What changed is the PR's squash commit, which the generated release log lists. Wording-only
         edits are exempt with `--skip-bump` (ci.yml passes it for the `wording` label or `[skip-bump]` in the PR
         title/body — the exemption is the author's explicit claim, visible on the PR).

Every finding is one line in the review's fixed shape: `[STOP] path:line — claim (rule)` (docs/REVIEW.md § Findings).
Exit 1 on any finding, 0 with a one-line summary otherwise; `--json` prints the findings as a list for
review_evidence.py. Stdlib only; needs `git` and a checkout that has the base ref (`fetch-depth: 0`).

`--tree` (#95) runs the leak check over EVERY file at `--head` instead of the added lines: a release commit pushed straight
to `main`, a squash of many PRs or a repo's first push is never a diff the PR scan sees. No base is needed and no bump check
runs; `ci.yml` runs it on every PR and every push to `main` (`make -C $BATON/context-db review-gate-tree` locally).
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import frontmatter as fmt  # noqa: E402
import leak_shapes  # noqa: E402

KIT = HERE.parents[1]
RULE_LEAK = "REVIEW.md § 2.1"
RULE_BUMP = "docs/contributing.md § Versioning"
ALLOW = "skills/kit-health/allow.txt"

# Shapes a diff scan adds to the shared list: they are PII/secret shapes, not environment facts, so kit-health's
# every-file scan (which has this machine's real values) does not need them, and a diff scan has nothing else.
PII_SHAPES = [
    (r"(?<![\w.+-])(?!git@)[\w.+-]+@(?!example\.(?:com|org|net)\b)(?![\w-]+(?:\.[\w-]+)*\.(?:invalid|test|example)(?!\.?[\w-]))(?!users\.noreply\.github\.com\b)(?!noreply\.github\.com\b)[\w-]+(?:\.[\w-]+)+(?![\w-])", "e-mail address"),
    (r"(?<![\w-])/(?:home|Users)/(?!<)(?!user\b)(?!runner\b)(?!\$)[A-Za-z][\w.-]*(?=[/\s`'\")]|$)", "home path (use `~`, `$HOME` or `<user>`)"),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "GitHub fine-grained token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b", "API key"),
    (r"\bglpat-[A-Za-z0-9_-]{20,}\b", "GitLab token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
]
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
    """{path: status} for the PR's own files (status A/M/D/R/C…), from the merge base so a base merge is not the PR's.
    NUL-separated (`-z`, #129): a path git would quote (non-ASCII, a tab, a quote) arrives verbatim; a rename or copy
    (`R100`/`C075`, two paths) is keyed by its new path."""
    mb = merge_base(base, head, cwd)
    out: dict[str, str] = {}
    rec = git("diff", "--name-status", "-z", "-M", "-C", mb, head, cwd=cwd).split("\0")
    i = 0
    while i < len(rec) and rec[i]:
        status = rec[i]
        paths = 2 if status[0] in "RC" else 1
        out[rec[i + paths]] = status[0]
        i += 1 + paths
    return out


def binary_files(base: str, head: str, cwd: Path = KIT) -> set[str]:
    """The PR's files git treats as binary (`--numstat` prints `-\t-`): no text hunks to scan."""
    mb = merge_base(base, head, cwd)
    out: set[str] = set()
    rec = git("diff", "--numstat", "-z", "-M", "-C", mb, head, cwd=cwd).split("\0")
    i = 0
    while i < len(rec) and rec[i]:
        added, deleted, path = (rec[i].split("\t", 2) + ["", ""])[:3]
        if path == "":             # rename/copy: `<a>\t<d>\t` then the old and the new path
            path, i = rec[i + 2], i + 3
        else:
            i += 1
        if added == "-" and deleted == "-":
            out.add(path)
    return out


def allowed_at(ref: str, cwd: Path = KIT) -> tuple[re.Pattern, ...]:
    """The allow-list as `ref` has it (#129): a PR never widens the list it is judged by — its own new lines count
    from the next PR on, after a human merged them."""
    text = show(ref, ALLOW, cwd)
    if text is None:
        return ()
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "allow.txt"
        f.write_text(text, encoding="utf-8")
        return leak_shapes.allowed(f)


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
    allow = allowed_at(merge_base(base, head, cwd), cwd)  # the BASE's allow-list, never the PR's (#129)
    binary = binary_files(base, head, cwd)
    out: list[str] = []
    for path, status in sorted(files.items()):
        if status == "D" or leak_shapes.skip_path(path) or path in binary:  # the one skip rule kit-health uses too
            continue
        if path == "context-db/bin/review_gate.py":  # this file names the shapes it scans for
            continue
        for n, text in added_lines(base, head, path, cwd):
            hits = leak_shapes.scan(text, shapes=shapes, rel=path, allow=allow)
            for _one, what, hit in hits:
                out.append(f"[STOP] {path}:{n} — {what} `{hit}` added; an environment's or a person's value never ships with the kit ({RULE_LEAK})")
    return out


def latest_date() -> str:
    """The latest `updated:` date that is not "in the future" anywhere: UTC today + 1 day (#145). The author stamps
    their local date; a UTC runner is up to a day behind UTC+14 and must not fail a PR another machine passes."""
    return (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()


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




def tree_files(head: str = "HEAD", cwd: Path = KIT) -> list[str]:
    """Every tracked file at `head`."""
    return [p for p in git("ls-tree", "-r", "--name-only", head, cwd=cwd).splitlines() if p]


def tree_findings(head: str = "HEAD", cwd: Path = KIT) -> tuple[list[str], int]:
    """(findings, files scanned): the leak shapes over every line of every tracked file at `head` (#95)."""
    shapes = leak_shapes.shapes() + [(re.compile(rx), what) for rx, what in PII_SHAPES]
    allow = allowed_at(head, cwd)  # a pushed tree has no base: the list as that tree has it
    out: list[str] = []
    files = [p for p in tree_files(head, cwd) if not leak_shapes.skip_path(p) and p != "context-db/bin/review_gate.py"]
    for path in files:
        text = show(head, path, cwd)
        if text is None:
            continue
        for n, what, hit in leak_shapes.scan(text, shapes=shapes, rel=path, allow=allow):
            out.append(f"[STOP] {path}:{n} — {what} `{hit}` in the tree; an environment's or a person's value never ships with the kit ({RULE_LEAK})")
    return out, len(files)


def bump_findings(base: str, head: str, cwd: Path = KIT, files: dict[str, str] | None = None) -> list[str]:
    files = changed_files(base, head, cwd) if files is None else files
    mb = merge_base(base, head, cwd)
    units = sorted({u for u in (unit_of(p) for p in files) if u})
    out: list[str] = []
    for unit in units:
        name = unit_name(unit)
        before, after = show(mb, unit, cwd), show(head, unit, cwd)
        if after is None:  # a removed unit needs nothing: the squash commit and the release log record it
            continue
        v_new, u_new = meta(after)
        if before is None:  # new unit
            if v_new is None:
                out.append(f"[STOP] {unit}:1 — new unit without an integer `metadata.version` ({RULE_BUMP})")
            continue
        v_old, u_old = meta(before)
        if v_new is None or v_old is None or v_new <= v_old:
            out.append(f"[STOP] {unit}:1 — files of `{name}` changed but `metadata.version` is {v_new!r} (base {v_old!r}); bump it, or mark the PR "
                       f"wording-only (`wording` label / `[skip-bump]`) ({RULE_BUMP})")
        if not u_new or (u_old and u_new < u_old):  # same day as the base's last bump is fine (two PRs in one day)
            out.append(f"[STOP] {unit}:1 — `metadata.updated` is {u_new!r} (base {u_old!r}); set it to the change's date ({RULE_BUMP})")
        elif u_new > latest_date():
            out.append(f"[STOP] {unit}:1 — `metadata.updated` {u_new!r} is in the future ({RULE_BUMP})")
    return out


def run(base: str, head: str = "HEAD", skip_bump: bool = False, cwd: Path = KIT,
        check: str = "all") -> tuple[list[str], dict[str, str]]:
    """(findings, changed files) for base..head — `check` is all | leak | bump. The one path the CLI and
    review_evidence.py take."""
    files = changed_files(base, head, cwd)
    findings: list[str] = []
    if check in ("all", "leak"):
        findings += leak_findings(base, head, cwd, files)
    if check in ("all", "bump") and not skip_bump:
        findings += bump_findings(base, head, cwd, files)
    return findings, files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tier-0 review gate: leak shapes on added lines, version/updated bumps")
    ap.add_argument("check", nargs="?", choices=("all", "leak", "bump"), default="all")
    ap.add_argument("--base", default="origin/main", help="the base ref (default origin/main)")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--skip-bump", action="store_true", help="wording-only PR: no version/updated check")
    ap.add_argument("--json", action="store_true", help="print the findings as a JSON list")
    ap.add_argument("--repo", type=Path, default=None, help="the repository (default: the working directory's git top level)")
    ap.add_argument("--tree", action="store_true", help="leak-scan every file at --head, not the diff (no base, no bump check)")
    a = ap.parse_args(argv)
    cwd = a.repo or repo_root()
    if a.tree:
        findings, n = tree_findings(a.head, cwd)
        if a.json:
            print(json.dumps(findings))
        else:
            for f in findings:
                print(f)
            print(f"review-gate --tree: {'FAIL' if findings else 'OK'} — {n} file(s) at {a.head}, {len(findings)} finding(s)")
        return 1 if findings else 0
    findings, files = run(a.base, a.head, a.skip_bump, cwd, a.check)
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
