#!/usr/bin/env python3
"""verified.py — the mechanical half of pr-open's `## Verified` section: run the repo's declared verify
command in the pushed worktree and print the block the body pastes, or check a drafted body's `## Verified`
section for a bare claim and (with --pr) a stale recorded head. Stdlib only; reuses evidence_check.py's
evidence-shape checks (`CLAIM_SEP`, `has_evidence`) rather than writing a second checker.

Usage:
  verified.py run --repo-dir <dir> --cmd "<cmd>" [--timeout <s>]
      Runs <cmd> through a shell in <dir>, with a <s>-second timeout (default 300). Prints the block
      `pr-open` pastes under `## Verified`: the command, <dir>'s HEAD (9-char, pr-merge.sh's display
      width), the exit code (or `timeout` when <s> ran out), then the last 15 lines of combined
      stdout+stderr — ANSI colour codes stripped, capped at 1200 bytes — in a fenced block.

  verified.py check <body-file | -> [--pr <owner/repo> <n>]
      Reads the body's `## Verified` section. A declared-command block (`Command:`/`Exit:` with a
      non-empty fenced tail), a cited-commands list (one `` `cmd` → `output` `` line per command —
      evidence_check.py's own CMD_OUTPUT shape), or an explicit `Not verified locally: <reason>` line
      all pass; bare prose ("tests pass", no output) is refused, one line per bare claim on stderr, in
      the same style as evidence_check.py. No `## Verified` heading at all passes too (same as
      evidence_check.py on a body with no `**Verified**` section — this tool checks claim shape, never
      whether the section exists). With --pr, also reads the section's recorded `Head:` sha and compares
      it with the PR's current head, printing `STALE <old> → <new>` when they differ — the diagram
      check's re-run-on-head-move reminder (SKILL.md § Diagrams step 5) applies here too.

Exit codes: 0 clean; 2 bad usage, an unreadable file, or a failed `gh`/`git` call; 3 (`check` only) a bare
claim and/or a stale recorded head.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(KIT / "context-db" / "bin"))
import evidence_check as ec  # noqa: E402 — the one place the evidence-shape regexes live
import fsutil  # noqa: E402 — the one `<file | ->` reader

try:
    import kit_profile  # type: ignore  # noqa: E402
except Exception:  # env store absent (bare checkout) — gh still runs with the plain environment
    kit_profile = None  # type: ignore[assignment]

MAX_LINES = 15
MAX_BYTES = 1200
HEAD_DISPLAY_LEN = 9  # pr-merge.sh's `head_of` convention — a short sha, never the full 40 chars
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
VERIFIED_HEAD = "## Verified"
NOT_VERIFIED_RE = re.compile(r"^Not verified locally:\s*\S")
HEAD_LINE_RE = re.compile(r"^Head:\s*`([0-9A-Fa-f]{7,40})`", re.MULTILINE)
FENCE_RE = re.compile(r"^(`{3,})[^\n]*\n(.*?)\n\1[ \t]*$", re.DOTALL | re.MULTILINE)
FENCE_OPEN_RE = re.compile(r"^(`{3,})")
FOOTER_RE = re.compile(r"^session `")  # the body's mandatory last line (SKILL.md step 1)


# ── run ──────────────────────────────────────────────────────────────────────────────────────
def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def bound_tail(text: str, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES) -> str:
    """The last `max_lines` lines of `text`, colour codes stripped, capped at `max_bytes` (kept from the
    tail end — a cut that lands mid-line drops that partial leading line, unless it is all there is)."""
    text = strip_ansi(text)
    lines = text.splitlines()
    out = "\n".join(lines[-max_lines:] if len(lines) > max_lines else lines)
    data = out.encode("utf-8", errors="replace")
    if len(data) > max_bytes:
        data = data[-max_bytes:]
        out = data.decode("utf-8", errors="ignore")
        if "\n" in out:
            out = out.split("\n", 1)[1]
    return out


def git_head(repo_dir: str) -> str:
    try:
        p = subprocess.run(["git", "-C", repo_dir, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"FAIL git -C {repo_dir} rev-parse HEAD timed out after 30s")
    if p.returncode != 0:
        raise SystemExit(f"FAIL git -C {repo_dir} rev-parse HEAD: {p.stderr.strip()[:300]}")
    return p.stdout.strip()[:HEAD_DISPLAY_LEN]


def run_cmd(repo_dir: str, cmd: str, timeout: int) -> dict:
    head = git_head(repo_dir)
    try:
        p = subprocess.run(cmd, shell=True, cwd=repo_dir, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout)
        exit_code: int | str = p.returncode
        output = p.stdout or ""
    except subprocess.TimeoutExpired as e:
        exit_code = "timeout"
        raw = e.output
        output = raw if isinstance(raw, str) else (raw.decode("utf-8", "replace") if raw else "")
    return {"command": cmd, "head": head, "exit": exit_code, "tail": bound_tail(output)}


def fence_for(text: str) -> str:
    """A backtick fence longer than any backtick run in `text` (minimum 3), so a stray ``` line in the
    tail can never close the block early."""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_block(r: dict, timeout: int) -> str:
    fence = fence_for(r["tail"])
    return "\n".join([
        f"Command: `{r['command']}` (timeout {timeout}s)",
        f"Head: `{r['head']}`",
        f"Exit: `{r['exit']}`",
        "",
        fence,
        r["tail"],
        fence,
    ])


# ── check ────────────────────────────────────────────────────────────────────────────────────
def verified_section(body: str) -> tuple[int, str] | None:
    """(the 1-based line the section's text starts on, that text) — None when the body carries no
    `## Verified` heading at all. The section ends at the next `## ` heading, the mandatory footer line
    (`session `<name>``), an HTML comment line (a diagram-plan marker can follow the footer too), or end
    of text — whichever comes first — so a `## Verified` section that is the body's last heading does not
    swallow the footer. A line inside a fenced tail (shape 1) is never mistaken for one of those endings,
    and blank lines never end the section themselves."""
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == VERIFIED_HEAD:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    in_fence = False
    fence_len = 0
    for j in range(start, len(lines)):
        line = lines[j]
        stripped = line.strip()
        if in_fence:
            if re.fullmatch(rf"`{{{fence_len},}}", stripped):
                in_fence = False
            continue
        if not stripped:
            continue
        m = FENCE_OPEN_RE.match(stripped)
        if m:
            in_fence = True
            fence_len = len(m.group(1))
            continue
        if line.startswith("## ") or stripped.startswith("<!--") or FOOTER_RE.match(stripped):
            end = j
            break
    while end > start and not lines[end - 1].strip():
        end -= 1
    return start + 1, "\n".join(lines[start:end])


def bare_claims(section_start: int, section: str) -> list[tuple[int, str]]:
    """Lines in a `## Verified` section with no evidence. A structured run block (`Command:`/`Exit:` with a
    non-empty fenced tail) and an explicit `Not verified locally: <reason>` line always pass; otherwise
    every non-blank line is a cited command (checked against evidence_check.py's own shapes, fed as a fake
    `cited · <line>` claim so the shape rules live in exactly one place) or it is bare."""
    stripped = section.strip()
    if not stripped:
        return [(section_start, "(empty ## Verified section)")]
    if NOT_VERIFIED_RE.match(stripped):
        return []
    if "Command:" in section and "Exit:" in section:
        m = FENCE_RE.search(section)
        if m and m.group(2).strip():
            return []
        return [(section_start, "declared-command block has no pasted output")]
    out: list[tuple[int, str]] = []
    for offset, raw in enumerate(section.splitlines()):
        line = raw.strip()
        if not line:
            continue
        claim = line[2:].strip() if line.startswith("- ") else line
        if not ec.has_evidence(f"cited{ec.CLAIM_SEP}{claim}"):
            out.append((section_start + offset, claim))
    return out


def sh(cmd: list[str], env: dict | None = None, timeout: int = 60) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"FAIL {' '.join(cmd[:3])}… timed out after {timeout}s")
    if p.returncode != 0:
        raise SystemExit(f"FAIL {' '.join(cmd[:3])}…: {p.stderr.strip()[:300]}")
    return p.stdout


def gh_env() -> dict:
    """os.environ plus `github.sandbox_token_prefix` (kit_profile is its one reader); unchanged without an
    env store — same pattern as diagram-plan.py's `gh_env`."""
    if kit_profile is None:
        return dict(os.environ)
    try:
        return kit_profile.gh_env()
    except (SystemExit, Exception):
        return dict(os.environ)


def pr_head(repo: str, n: int) -> str:
    out = sh(["gh", "api", f"repos/{repo}/pulls/{n}", "--jq", ".head.sha"], gh_env())
    return out.strip()[:HEAD_DISPLAY_LEN]


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def cmd_run(a: argparse.Namespace) -> int:
    r = run_cmd(a.repo_dir, a.cmd, a.timeout)
    print(render_block(r, a.timeout))
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    try:
        text = fsutil.read_arg(a.file)
    except OSError as e:
        print(f"verified: {e}", file=sys.stderr)
        return 2
    sec = verified_section(text)
    rc = 0
    if sec is None:
        print("verified: ok — no ## Verified section")
        return 0
    start, section = sec
    for lineno, claim in bare_claims(start, section):
        print(f"verified: {lineno}: {claim}", file=sys.stderr)
        rc = 3
    if a.pr:
        repo, n = a.pr[0], int(a.pr[1])
        m = HEAD_LINE_RE.search(section)
        if m is not None:
            old = m.group(1)[:HEAD_DISPLAY_LEN]
            new = pr_head(repo, n)
            if old != new:
                print(f"STALE {old} → {new}")
                rc = 3
    if rc == 0:
        print("verified: ok")
    return rc


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    r = sub.add_parser("run", help="run the declared verify command and print the ## Verified block")
    r.add_argument("--repo-dir", required=True, help="the worktree the command runs in (on the pushed head)")
    r.add_argument("--cmd", required=True, help="the shell command (verify.repos.<owner/repo>.cmd)")
    r.add_argument("--timeout", type=int, default=300, help="seconds before the command is killed (default 300)")

    c = sub.add_parser("check", help="check a drafted body's ## Verified section")
    c.add_argument("file", help="the PR body file, or - for stdin")
    c.add_argument("--pr", nargs=2, metavar=("OWNER/REPO", "N"), help="also compare the recorded head with the PR's current one")

    a = ap.parse_args(argv[1:])
    if a.mode == "run":
        return cmd_run(a)
    return cmd_check(a)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
