#!/usr/bin/env python3
"""codeowners.py — who must review a PR, read from the CODEOWNERS file GitHub itself evaluates, never from
prose. Stdlib only; `gh` is the only network path (`run_gh`, the one function a test replaces).

Usage:
  codeowners.py resolve <owner/repo> --pr <n>
  codeowners.py resolve <owner/repo> --paths <path> [<path> ...]
      Fetches CODEOWNERS from the repo's default branch (`.github/CODEOWNERS`, then `CODEOWNERS`, then
      `docs/CODEOWNERS` — GitHub's own lookup order), matches every changed path (a --pr's files, a rename's
      old name too) with GitHub's rule — the LAST matching line wins, per path — and prints the union of
      owners, one per line, in first-seen order: `team:<slug>` or `user:<login>`. With --pr the PR author is
      left out (GitHub refuses a review request on its own author; a stderr note says so). An owner that is
      an e-mail address cannot be requested: a stderr note, no line.

  codeowners.py check <owner/repo> --pr <n> [--ignore <login> ...]
      The head-move reminder. Compares the resolved owners with the PR's current review requests and the
      logins that already reviewed, prints ONE line, `OWNERS OK …` or `OWNERS CHANGED +<owner> … -<owner> …`
      (`+` = an owner nobody requested, `-` = a request for something that no longer owns a touched path;
      --ignore exempts a login, e.g. the review bot, from the `-` side), and exits 3 on a change.

Pattern grammar (GitHub's gitignore subset): `#` comments; a leading `/` anchors to the root, and so does a
`/` anywhere but the end; a trailing `/` is a directory (everything beneath it); `*` stays inside one path
segment; `**` crosses segments; `?` is one non-`/` character; a pattern whose last segment is a plain name
also owns what is beneath it (`docs/*` owns docs/a.md, not docs/sub/a.md). GitHub supports no negation
(`!`), no `[…]` ranges and no `\\` escapes other than `\\#`: those are read literally.
A matching line with no owners un-owns the path.

Exit codes: 0 owners printed (resolve) / set unchanged (check); 2 bad usage or a failed `gh` call; 3 nothing
matched or no CODEOWNERS file (resolve: nothing to request), owner set changed (check).
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
try:
    import kit_profile  # type: ignore  # noqa: E402
except Exception:  # env store absent (bare checkout) — gh still runs with the plain environment
    kit_profile = None  # type: ignore[assignment]

LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


# ── the matching rule (pure) ─────────────────────────────────────────────────────────────────
def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    """[(pattern, owners)] in file order. Comments and blanks dropped; an unescaped `#` after whitespace ends
    the line; `\\#` at the start of a pattern is a literal `#`."""
    rules: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.split(r"(?<!\\)\s+#", line, maxsplit=1)[0]
        parts = line.split()
        if not parts:
            continue
        pattern = parts[0][1:] if parts[0].startswith("\\#") else parts[0]
        rules.append((pattern, parts[1:]))
    return rules


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """The compiled regex a CODEOWNERS pattern means, matched against a repo-relative path with no leading `/`."""
    dir_only = pattern.endswith("/")
    body = pattern.rstrip("/")
    anchored = body.startswith("/") or "/" in body
    body = body.lstrip("/")
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("/**", i) and i + 3 == len(body):
            out.append("/.*")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    head = "^" if anchored else "^(?:.*/)?"
    # A literal final segment names a directory too (its contents are owned); one with a wildcard owns only the
    # entries it matches — GitHub: `docs/*` owns docs/a.md, not docs/sub/a.md.
    literal_last = not re.search(r"[*?]", body.rsplit("/", 1)[-1])
    tail = "/.*$" if dir_only else ("(?:/.*)?$" if literal_last else "$")
    return re.compile(head + "".join(out) + tail, re.DOTALL)


def owners_for(path: str, rules: list[tuple[str, list[str]]]) -> list[str] | None:
    """The owners of the LAST rule matching `path`; None when no rule matches (a matching rule with no owners
    returns [] — the path is explicitly un-owned)."""
    path = path.lstrip("/")
    hit: list[str] | None = None
    for pattern, owners in rules:
        if pattern_regex(pattern).match(path):
            hit = owners
    return hit


def normalise_owner(owner: str) -> str | None:
    """`@org/team` → `team:team`, `@login` → `user:login`; an e-mail owner → None (not requestable)."""
    if not owner.startswith("@"):
        return None
    name = owner[1:]
    if "/" in name:
        return "team:" + name.split("/", 1)[1]
    return "user:" + name


def resolve_owners(paths: list[str], rules: list[tuple[str, list[str]]]) -> tuple[list[str], list[str]]:
    """(requestable owners, first-seen, deduplicated; skipped e-mail owners)."""
    seen: dict[str, None] = {}
    skipped: dict[str, None] = {}
    for p in paths:
        for o in owners_for(p, rules) or []:
            n = normalise_owner(o)
            if n is None:
                skipped[o] = None
            else:
                seen[n] = None
    return list(seen), list(skipped)


def compare(resolved: list[str], requested: list[str], reviewed: list[str], ignore: list[str]) -> tuple[list[str], list[str]]:
    """(owners nobody requested nor reviewed, requests that own no touched path and are not ignored)."""
    done = set(requested) | {"user:" + r for r in reviewed}
    ign = {"user:" + i for i in ignore}
    missing = [o for o in resolved if o not in done]
    stale = [r for r in requested if r not in resolved and r not in ign]
    return missing, stale


# ── gh (the one network path) ────────────────────────────────────────────────────────────────
def gh_env() -> dict:
    if kit_profile is None:
        return dict(os.environ)
    try:
        return kit_profile.gh_env()
    except (SystemExit, Exception):
        return dict(os.environ)


def run_gh(args: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, env=gh_env(), timeout=60)
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, "", f"{type(e).__name__}: {e}"
    return p.returncode, p.stdout, p.stderr


def die(msg: str) -> "SystemExit":
    return SystemExit(f"FAIL {msg}")


def fetch_codeowners(repo: str) -> str | None:
    """The first CODEOWNERS file found on the default branch; None when none of the three exists. A failure
    other than 404 stops the run: a flaky read is never 'no owners'."""
    for loc in LOCATIONS:
        rc, out, err = run_gh(["api", f"repos/{repo}/contents/{loc}", "-H", "Accept: application/vnd.github.raw"])
        if rc == 0:
            return out
        if "404" in err or "Not Found" in err:
            continue
        raise die(f"gh api repos/{repo}/contents/{loc}: {err.strip()[:300]}")
    return None


def pr_paths(repo: str, n: int) -> list[str]:
    rc, out, err = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{n}/files",
                           "--jq", ".[] | .filename, (.previous_filename // empty)"])
    if rc != 0:
        raise die(f"gh api repos/{repo}/pulls/{n}/files: {err.strip()[:300]}")
    return [l for l in out.splitlines() if l]


def pr_info(repo: str, n: int) -> dict:
    """author login, requested user logins and team slugs."""
    rc, out, err = run_gh(["api", f"repos/{repo}/pulls/{n}", "--jq",
                           '[.user.login] + [.requested_reviewers[].login | "u:" + .] + '
                           '[.requested_teams[].slug | "t:" + .] | .[]'])
    if rc != 0:
        raise die(f"gh api repos/{repo}/pulls/{n}: {err.strip()[:300]}")
    lines = [l for l in out.splitlines() if l]
    requested = [("user:" + l[2:]) if l.startswith("u:") else ("team:" + l[2:]) for l in lines[1:]]
    return {"author": lines[0] if lines else "", "requested": requested}


def pr_reviewers(repo: str, n: int) -> list[str]:
    rc, out, err = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{n}/reviews", "--jq", ".[].user.login"])
    if rc != 0:
        raise die(f"gh api repos/{repo}/pulls/{n}/reviews: {err.strip()[:300]}")
    return sorted({l for l in out.splitlines() if l})


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def resolved_for_pr(repo: str, n: int) -> tuple[list[str], dict]:
    text = fetch_codeowners(repo)
    info = pr_info(repo, n)
    if text is None:
        return [], info
    owners, skipped = resolve_owners(pr_paths(repo, n), parse_codeowners(text))
    for s in skipped:
        print(f"codeowners: {s} is an e-mail owner — not requestable, skipped", file=sys.stderr)
    return [o for o in owners if o != "user:" + info["author"]], info


def cmd_resolve(a: argparse.Namespace) -> int:
    text = fetch_codeowners(a.repo)
    if text is None:
        print(f"codeowners: no CODEOWNERS file on {a.repo}'s default branch ({', '.join(LOCATIONS)}) — nothing to request",
              file=sys.stderr)
        return 3
    author = ""
    if a.pr is not None:
        paths = pr_paths(a.repo, a.pr)
        author = pr_info(a.repo, a.pr)["author"]
    else:
        paths = a.paths
    owners, skipped = resolve_owners(paths, parse_codeowners(text))
    for s in skipped:
        print(f"codeowners: {s} is an e-mail owner — not requestable, skipped", file=sys.stderr)
    if author and "user:" + author in owners:
        print(f"codeowners: user:{author} is the PR author — GitHub cannot request it, left out", file=sys.stderr)
        owners = [o for o in owners if o != "user:" + author]
    if not owners:
        print("codeowners: no CODEOWNERS line owns a changed path — nothing to request", file=sys.stderr)
        return 3
    print("\n".join(owners))
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    resolved, info = resolved_for_pr(a.repo, a.pr)
    missing, stale = compare(resolved, info["requested"], pr_reviewers(a.repo, a.pr), a.ignore or [])
    if not missing and not stale:
        print("OWNERS OK " + (" ".join(resolved) or "none"))
        return 0
    print("OWNERS CHANGED " + " ".join([f"+{m}" for m in missing] + [f"-{s}" for s in stale]))
    return 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve a PR's reviewers from the live CODEOWNERS file.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="print team:<slug> / user:<login> lines")
    r.add_argument("repo", help="<owner>/<repo>")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--pr", type=int)
    g.add_argument("--paths", nargs="+")
    c = sub.add_parser("check", help="one line: OWNERS OK / OWNERS CHANGED (exit 3)")
    c.add_argument("repo", help="<owner>/<repo>")
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--ignore", action="append", help="a login exempt from the '-' side (e.g. the review bot)")
    a = ap.parse_args(argv)
    return cmd_resolve(a) if a.cmd == "resolve" else cmd_check(a)


if __name__ == "__main__":
    raise SystemExit(main())
