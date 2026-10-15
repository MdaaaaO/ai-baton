#!/usr/bin/env python3
"""codeowners.py — who must review a PR, read from the CODEOWNERS file GitHub itself evaluates, never from
prose. Stdlib only; `gh` is the only network path (`run_gh`, the one function a test replaces).

Usage:
  codeowners.py resolve <owner/repo> --pr <n>
  codeowners.py resolve <owner/repo> --paths <path> [<path> ...]
      Reads the repo (with --pr: the PR) first — a repo or PR the token cannot read is a FAIL, never "no
      owners". Then fetches CODEOWNERS (`.github/CODEOWNERS`, then `CODEOWNERS`, then `docs/CODEOWNERS` —
      GitHub's own lookup order) from the branch GitHub evaluates: with --pr the PR's BASE branch, with
      --paths the default branch. Matches every changed path (a --pr's files, a rename's old name too) with
      GitHub's rule — the LAST matching line wins, per path — and prints the union of owners, one per line,
      in first-seen order: `team:<slug>` or `user:<login>`. With --pr the PR author is left out (GitHub
      refuses a review request on its own author; a stderr note says so). An owner that is an e-mail address
      cannot be requested: a stderr note, no line.

  codeowners.py check <owner/repo> --pr <n> [--ignore <login> ...]
      The head-move reminder: prints ONE line, `OWNERS OK …` or `OWNERS CHANGED +<owner> …` (exit 3) naming
      each resolved owner that is neither requested nor satisfied by a review. A user is satisfied by its own
      review; a team by a review from one of its members (`orgs/<org>/teams/<slug>/members`), because GitHub
      drops a team from the requests once a member reviews. When the membership is not readable (403/404, a
      secret team or a token without read:org) the fallback counts a team as satisfied when it was ever
      requested (now, or a `review_requested` event in the PR timeline) and any review exists from a login
      other than the author and the --ignore logins — an approximation, named in a stderr note. The check
      only asks for MISSING owners: a request for a non-owner (one the author added by hand) is one `note:`
      line on stderr, never part of the line; --ignore (e.g. the review bot) leaves a login out of that note.

Pattern grammar (GitHub's gitignore subset): a line starting with `#` is a comment, and ` #` after the owners
starts one; a leading `/` anchors to the root, and so does a `/` anywhere but the end; a trailing `/` is a
directory (everything beneath it); `*` stays inside one path segment; `**` crosses segments; `?` is one
non-`/` character; a pattern whose last segment is a plain name also owns what is beneath it (`docs/*` owns
docs/a.md, not docs/sub/a.md). GitHub supports no negation (`!`), no `[…]` ranges and no `\\` escapes:
those are read literally. A matching line with no owners un-owns the path.

Exit codes: 0 owners printed (resolve) / no owner missing (check); 2 bad usage or a failed read — the repo or
PR is not readable, or a `gh` call failed (stderr: `FAIL …`) — never "nothing to request"; 3 the repo is
readable but no CODEOWNERS line owns a changed path or there is no CODEOWNERS file (resolve: nothing to
request), an owner is missing (check).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.parse
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
    """[(pattern, owners)] in file order. Blanks and lines starting with `#` dropped; a `#` after whitespace ends
    the line. No escapes: a `\\` is part of the pattern."""
    rules: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+#", line, maxsplit=1)[0].split()
        if parts:
            rules.append((parts[0], parts[1:]))
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


def compare(resolved: list[str], requested: list[str], satisfied: set[str], ignore: list[str]) -> tuple[list[str], list[str]]:
    """(owners neither requested nor satisfied by a review, requests that own no touched path and are not
    ignored — the second list is informational only, never an action)."""
    done = set(requested) | satisfied
    ign = {"user:" + i for i in ignore}
    missing = [o for o in resolved if o not in done]
    extra = [r for r in requested if r not in resolved and r not in ign]
    return missing, extra


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


def die(msg: str) -> SystemExit:
    """`FAIL <msg>` on stderr and the exit-2 SystemExit to raise: a failed read is never a negative answer."""
    print(f"FAIL {msg}", file=sys.stderr)
    return SystemExit(2)


def is_missing(err: str) -> bool:
    return "404" in err or "Not Found" in err


def repo_default_branch(repo: str) -> str:
    """The repo's default branch — and the readability gate: a repo the token cannot see (or a typo) is a FAIL,
    so only a readable repo with no CODEOWNERS file reaches 'nothing to request'."""
    rc, out, err = run_gh(["api", f"repos/{repo}", "--jq", ".default_branch"])
    if rc != 0:
        raise die(f"repo not readable — gh api repos/{repo}: {err.strip()[:300]}")
    return out.strip()


def fetch_codeowners(repo: str, ref: str | None = None) -> str | None:
    """The first CODEOWNERS file found on `ref` (None: the default branch); None when none of the three exists.
    A failure other than 404 stops the run: a flaky read is never 'no owners'."""
    query = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
    for loc in LOCATIONS:
        rc, out, err = run_gh(["api", f"repos/{repo}/contents/{loc}{query}", "-H", "Accept: application/vnd.github.raw"])
        if rc == 0:
            return out
        if is_missing(err):
            continue
        raise die(f"gh api repos/{repo}/contents/{loc}{query}: {err.strip()[:300]}")
    return None


def pr_paths(repo: str, n: int) -> list[str]:
    rc, out, err = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{n}/files",
                           "--jq", ".[] | .filename, (.previous_filename // empty)"])
    if rc != 0:
        raise die(f"gh api repos/{repo}/pulls/{n}/files: {err.strip()[:300]}")
    return [l for l in out.splitlines() if l]


def pr_info(repo: str, n: int) -> dict:
    """author login, base branch, requested user logins and team slugs — and the readability gate for --pr."""
    rc, out, err = run_gh(["api", f"repos/{repo}/pulls/{n}", "--jq",
                           '[.user.login, "b:" + .base.ref] + [.requested_reviewers[].login | "u:" + .] + '
                           '[.requested_teams[].slug | "t:" + .] | .[]'])
    if rc != 0:
        raise die(f"repo or PR not readable — gh api repos/{repo}/pulls/{n}: {err.strip()[:300]}")
    lines = [l for l in out.splitlines() if l]
    base = next((l[2:] for l in lines[1:] if l.startswith("b:")), None)
    requested = [("user:" if l.startswith("u:") else "team:") + l[2:] for l in lines[1:] if l[:2] in ("u:", "t:")]
    return {"author": lines[0] if lines else "", "base": base, "requested": requested}


def pr_reviewers(repo: str, n: int) -> list[str]:
    rc, out, err = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{n}/reviews", "--jq", ".[].user.login"])
    if rc != 0:
        raise die(f"gh api repos/{repo}/pulls/{n}/reviews: {err.strip()[:300]}")
    return sorted({l for l in out.splitlines() if l})


def team_members(org: str, slug: str) -> set[str] | None:
    """The team's logins (child teams included); None when GitHub will not show them (403/404)."""
    rc, out, err = run_gh(["api", "--paginate", f"orgs/{org}/teams/{slug}/members", "--jq", ".[].login"])
    if rc == 0:
        return {l for l in out.splitlines() if l}
    if is_missing(err) or "403" in err or "Forbidden" in err:
        return None
    raise die(f"gh api orgs/{org}/teams/{slug}/members: {err.strip()[:300]}")


def ever_requested_teams(repo: str, n: int) -> set[str]:
    rc, out, err = run_gh(["api", "--paginate", f"repos/{repo}/issues/{n}/timeline", "--jq",
                           '.[] | select(.event == "review_requested") | .requested_team.slug // empty'])
    if rc != 0:
        raise die(f"gh api repos/{repo}/issues/{n}/timeline: {err.strip()[:300]}")
    return {"team:" + l for l in out.splitlines() if l}


def satisfied_owners(repo: str, n: int, resolved: list[str], info: dict, ignore: list[str]) -> set[str]:
    """The resolved owners a review already covers: a user by its own review, a team by a member's review (or,
    membership unreadable, by the fallback in the module docstring)."""
    reviewers = [r for r in pr_reviewers(repo, n) if r != info["author"]]
    done = {"user:" + r for r in reviewers}
    others = [r for r in reviewers if r not in ignore]
    org = repo.split("/", 1)[0]
    ever: set[str] | None = None
    for o in resolved:
        if not o.startswith("team:") or o in info["requested"] or not reviewers:
            continue
        members = team_members(org, o[5:])
        if members is not None:
            if members & set(reviewers):
                done.add(o)
            continue
        if ever is None:
            ever = ever_requested_teams(repo, n) | {r for r in info["requested"] if r.startswith("team:")}
        if o in ever and others:
            done.add(o)
            print(f"codeowners: note: {o} membership not readable — counted as reviewed: it was requested and "
                  f"{others[0]} reviewed (fallback)", file=sys.stderr)
    return done


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def report_skipped(skipped: list[str]) -> None:
    for s in skipped:
        print(f"codeowners: {s} is an e-mail owner — not requestable, skipped", file=sys.stderr)


def resolved_for_pr(repo: str, n: int) -> tuple[list[str], dict]:
    info = pr_info(repo, n)  # first: the readability gate, and the base branch GitHub evaluates CODEOWNERS on
    text = fetch_codeowners(repo, info["base"])
    if text is None:
        return [], info
    owners, skipped = resolve_owners(pr_paths(repo, n), parse_codeowners(text))
    report_skipped(skipped)
    return [o for o in owners if o != "user:" + info["author"]], info


def cmd_resolve(a: argparse.Namespace) -> int:
    author = ""
    if a.pr is not None:
        info = pr_info(a.repo, a.pr)
        author, where = info["author"], f"base branch {info['base']}" if info["base"] else "default branch"
        text = fetch_codeowners(a.repo, info["base"])
    else:
        where = f"default branch {repo_default_branch(a.repo)}"
        text = fetch_codeowners(a.repo)
    if text is None:
        print(f"codeowners: no CODEOWNERS file on {a.repo}'s {where} ({', '.join(LOCATIONS)}) — nothing to request",
              file=sys.stderr)
        return 3
    paths = pr_paths(a.repo, a.pr) if a.pr is not None else a.paths
    owners, skipped = resolve_owners(paths, parse_codeowners(text))
    report_skipped(skipped)
    if author and "user:" + author in owners:
        print(f"codeowners: user:{author} is the PR author — GitHub cannot request it, left out", file=sys.stderr)
        owners = [o for o in owners if o != "user:" + author]
    if not owners:
        print("codeowners: no CODEOWNERS line owns a changed path — nothing to request", file=sys.stderr)
        return 3
    print("\n".join(owners))
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    ignore = a.ignore or []
    resolved, info = resolved_for_pr(a.repo, a.pr)
    satisfied = satisfied_owners(a.repo, a.pr, resolved, info, ignore)
    missing, extra = compare(resolved, info["requested"], satisfied, ignore)
    if extra:
        print("codeowners: note: requested but owning no changed path (left as is): " + " ".join(extra),
              file=sys.stderr)
    if not missing:
        print("OWNERS OK " + (" ".join(resolved) or "none"))
        return 0
    print("OWNERS CHANGED " + " ".join(f"+{m}" for m in missing))
    return 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve a PR's reviewers from the live CODEOWNERS file.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="print team:<slug> / user:<login> lines")
    r.add_argument("repo", help="<owner>/<repo>")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--pr", type=int)
    g.add_argument("--paths", nargs="+")
    c = sub.add_parser("check", help="one line: OWNERS OK / OWNERS CHANGED +<missing owner> (exit 3)")
    c.add_argument("repo", help="<owner>/<repo>")
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--ignore", action="append", help="a login left out of the extra-request note and the team fallback (e.g. the review bot)")
    a = ap.parse_args(argv)
    return cmd_resolve(a) if a.cmd == "resolve" else cmd_check(a)


if __name__ == "__main__":
    raise SystemExit(main())
