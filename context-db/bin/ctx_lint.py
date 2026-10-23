#!/usr/bin/env python3
"""ctx_lint.py — the mechanical half of the store lint (`/kit-health lint`, docs/knowledge-lint.md).

Read-only: it never writes the store, never stamps, never asks a model. The judgement half (contradictions and
prose-stated stale facts) is one no-tool fork per changed domain, planned by `plan` and fed by `bundle`.

  ctx_lint.py check  [--root DIR] [--offline] [--gh CMD] [--max-keys N] [--today YYYY-MM-DD]
  ctx_lint.py plan   [--root DIR] [--stamp-file FILE | --since DATE] [--max-forks N] [--sonnet-over BYTES]
  ctx_lint.py bundle <domain> [--root DIR]

`check` prints one finding per line, or `CLEAN`:
  ORPHAN <doc>                                  no inbound link or path mention, except from a generated index
  DEAD <doc> <target>                           an internal Markdown link that resolves to nothing
  STALE <doc> <claim> · <system of record>      a tracker key stated in a current-state section whose state differs
  AGED <doc> <why>                              a closed or done doc untouched past the archive rule (gen_sessions.ARCHIVE_DAYS)
  UNCHECKED <doc> <claim>                       a tracker claim this run could not read (offline, failed read, over the cap)
A claim is read only from the current-state sections: Tracker & links, Goal, Open PRs and the newest Remaining
work bullet. History is never a claim: Session log, archive/, logs, archived docs and any dated bullet.
Tracker states come from ONE batched GraphQL query per repository through `gh api graphql`; at most --max-keys
(100) distinct keys are read per run, the rest are UNCHECKED, and so is every key of a repository whose read failed.

`plan` prints which domains a lint run should read since the last lint stamp (`FORK <domain> <model> <bytes>`, or
`NOT READ <domain> <bytes>` past the fork cap); `bundle` prints one domain's docs as the fork's input. The stamp is
written by the session step, never here; this script only reads it.

Exit codes: 0 success (CLEAN, or only UNCHECKED lines; `plan` and `bundle` printed their output);
1 a `gh` read failed (message on stderr; the affected claims are UNCHECKED) and nothing else was found;
2 bad usage (unknown domain, unreadable root, malformed date); 3 at least one finding (ORPHAN, DEAD, STALE, AGED).

`--offline` (or CTX_LINT_OFFLINE=1) never calls gh: every tracker claim is UNCHECKED. `--gh CMD` (or CTX_LINT_GH)
names the gh executable, which is how tests inject a fake responder. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontmatter as fm  # noqa: E402 — the one frontmatter parser
import gen_index as gi  # noqa: E402 — the catalog's skip lists
import gen_sessions  # noqa: E402 — owns ARCHIVE_DAYS, the archive rule's one constant
import kit_profile  # noqa: E402 — the one content-root resolver

MAX_KEYS = 100
MAX_FORKS = 6
SONNET_OVER = 60 * 1024
GH_TIMEOUT = 60
DEFAULT_GENERATED = ("INDEX.md", "SESSION_INDEX.md", "sessions/archive/INDEX.md")
LINK_SKIP_DIRS = {"bin", "_templates", "handoff", "memory", "state"}  # engine and harness files, never store prose
FINDING_ORDER = ("ORPHAN", "DEAD", "STALE", "AGED", "UNCHECKED")

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s")
DATED = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)?\W{0,3}(\d{4}-\d{2}-\d{2})\b")
URL_KEY = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)")
SHORT_KEY = re.compile(r"(?<![\w/.-])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)\b")
STATE_WORD = re.compile(r"\b(open|opened|in review|draft|merged|closed|resolved|done|shipped|landed)\b", re.IGNORECASE)
SENTENCE_END = re.compile(r"[.;!?](?=\s)")
NEGATING = {"not", "no", "never", "was", "were", "once", "until", "if", "when", "to", "will", "be", "been", "get",
            "gets", "needs", "need", "pending", "awaiting", "before", "after"}
CURRENT_SECTIONS = (("tracker", re.compile(r"^(?:tracker|jira)\s*&\s*links\b", re.IGNORECASE)),
                    ("goal", re.compile(r"^goal\b", re.IGNORECASE)),
                    ("prs", re.compile(r"^open prs\b", re.IGNORECASE)),
                    ("remaining", re.compile(r"^remaining work\b", re.IGNORECASE)))


@dataclass
class Doc:
    rel: str
    path: Path
    text: str
    meta: dict
    domain: str

    @property
    def history(self) -> bool:
        """A doc that records history on purpose: never a source of STALE claims, ORPHAN or AGED."""
        return ("archive" in Path(self.rel).parts or self.meta.get("type") == "log"
                or self.meta.get("status") == "archived")


@dataclass
class Claim:
    doc: str
    repo: str
    number: int
    state: str          # "open" | "merged" | "closed" — what the doc says
    verdict: str = ""   # "" until read; "STALE" | "OK" | "UNCHECKED"
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass
class Report:
    lines: list[tuple[str, str]] = field(default_factory=list)
    gh_failed: bool = False

    def add(self, kind: str, text: str) -> None:
        self.lines.append((kind, text))

    def findings(self) -> int:
        return sum(1 for k, _ in self.lines if k != "UNCHECKED")

    def render(self) -> list[str]:
        out = sorted(set(self.lines), key=lambda kt: (FINDING_ORDER.index(kt[0]), kt[1]))
        return [f"{k} {t}" for k, t in out]


Runner = Callable[[list[str]], "tuple[int, str, str]"]


# ── the store ────────────────────────────────────────────────────────────────────────────────────

def generated_paths(root: Path) -> set[str]:
    """The generated indexes (store-relative): the store file's `generated` list, else the engine's three."""
    try:
        listed = json.loads((root / "ctx-store.json").read_text(encoding="utf-8")).get("generated")
    except (OSError, ValueError, AttributeError):
        listed = None
    return {str(p) for p in listed} if isinstance(listed, list) and listed else set(DEFAULT_GENERATED)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def walk_markdown(root: Path, skip_dirs: set[str]) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs and not d.startswith("."))
        found += [Path(dirpath) / f for f in sorted(filenames) if f.endswith(".md")]
    return found


def load_docs(root: Path) -> tuple[list[Doc], list[Doc]]:
    """(catalog docs, linkers). Catalog docs are what the INDEX lists; linkers add the live session files and any
    README, minus the generated indexes — the only places a doc is allowed to be listed without being linked."""
    generated = generated_paths(root)
    docs: list[Doc] = []
    for p in walk_markdown(root, set(gi.SKIP_DIRS)):
        rel = p.relative_to(root).as_posix()
        if p.name in gi.SKIP_FILES or rel in generated:
            continue
        text = read_text(p)
        if text is None:
            continue
        meta = fm.load_flat(p, errors="replace") or {}
        docs.append(Doc(rel, p, text, meta, meta.get("domain") or Path(rel).parts[0]))
    linkers = list(docs)
    seen = {d.rel for d in docs}
    for p in walk_markdown(root, LINK_SKIP_DIRS):
        rel = p.relative_to(root).as_posix()
        if rel in seen or rel in generated:
            continue
        text = read_text(p)
        if text is not None:
            linkers.append(Doc(rel, p, text, {}, ""))
    return docs, linkers


def prose_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) outside fenced code blocks."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            out.append((n, line))
    return out


# ── dead links and orphans ───────────────────────────────────────────────────────────────────────

def link_target(raw: str) -> str | None:
    """The file part of a Markdown link target when it is an internal path worth resolving, else None."""
    target = raw.split("#", 1)[0].split("?", 1)[0]
    if (not target or "://" in raw or raw.startswith(("#", "/", "mailto:")) or "<" in raw or "{" in raw
            or ("/" not in target and "." not in target)):
        return None
    return target


def resolve_link(doc: Doc, root: Path, target: str) -> tuple[Path | None, bool]:
    """(path it resolves to, whether the store can judge it). A target outside the store is not judged."""
    for base in (doc.path.parent, root):
        cand = Path(os.path.normpath(base / target))
        try:
            cand.relative_to(root)
        except ValueError:
            if base == doc.path.parent:
                continue
            return None, False
        if cand.exists():
            return cand, True
    outside = Path(os.path.normpath(doc.path.parent / target))
    try:
        outside.relative_to(root)
    except ValueError:
        return None, False
    return None, True


def check_links(root: Path, docs: list[Doc], linkers: list[Doc], report: Report) -> dict[str, set[str]]:
    """DEAD findings for the catalog docs; returns inbound[target rel] = {linker rel} from every linker's links."""
    inbound: dict[str, set[str]] = {}
    catalog = {d.rel for d in docs}
    for doc in linkers:
        for _, line in prose_lines(doc.text):
            for m in LINK.finditer(line):
                target = link_target(m.group(1))
                if target is None:
                    continue
                path, judged = resolve_link(doc, root, target)
                if path is None:
                    if judged and doc.rel in catalog:
                        report.add("DEAD", f"{doc.rel} {target}")
                    continue
                inbound.setdefault(path.relative_to(root).as_posix(), set()).add(doc.rel)
    return inbound


def mentioned_in(rel: str, text: str) -> bool:
    """The doc's store path (`domain/slug.md` or `domain/slug`) appears in the text — how store docs usually refer
    to each other (a backticked path, a doc key), beside a real link."""
    stem = rel[:-3] if rel.endswith(".md") else rel
    if stem not in text:
        return False
    return re.search(rf"(?<![\w/.-]){re.escape(stem)}(?:\.md)?(?![\w/-])", text) is not None


def exempt_from_orphan(doc: Doc) -> bool:
    return doc.history or doc.path.name == "README.md"


def check_orphans(docs: list[Doc], linkers: list[Doc], inbound: dict[str, set[str]], report: Report) -> None:
    for doc in docs:
        if exempt_from_orphan(doc):
            continue
        if any(src != doc.rel for src in inbound.get(doc.rel, ())):
            continue
        if any(src.rel != doc.rel and mentioned_in(doc.rel, src.text) for src in linkers):
            continue
        report.add("ORPHAN", doc.rel)


# ── docs past the archive rule ───────────────────────────────────────────────────────────────────

def check_aged(docs: list[Doc], today: date, report: Report) -> None:
    """A finished doc (status closed or done) untouched for more than the engine's archive window still sits in
    the live catalog: `archive` moves it out. The window is gen_sessions.ARCHIVE_DAYS, never a second constant."""
    days = gen_sessions.ARCHIVE_DAYS
    for doc in docs:
        if doc.history or doc.meta.get("status") not in ("closed", "done"):
            continue
        try:
            age = (today - date.fromisoformat(doc.meta.get("updated", "")[:10])).days
        except ValueError:
            continue
        if age > days:
            report.add("AGED", f"{doc.rel} status {doc.meta['status']}, untouched {age} days (> {days}) — archive it")


# ── tracker claims ───────────────────────────────────────────────────────────────────────────────

def sections(text: str) -> dict[str, list[tuple[int, str]]]:
    """The current-state sections of a doc by kind: lines (number, text) from the heading to the next heading of
    the same or a higher level. Fenced code is dropped."""
    found: dict[str, list[tuple[int, str]]] = {}
    kind, level = "", 0
    for n, line in prose_lines(text):
        h = HEADING.match(line)
        if h:
            if kind and len(h.group(1)) <= level:
                kind = ""
            if not kind:
                for name, pat in CURRENT_SECTIONS:
                    if pat.match(h.group(2)):
                        kind, level = name, len(h.group(1))
                        found.setdefault(name, [])
                        break
            continue
        if kind:
            found[kind].append((n, line))
    return found


def units(lines: list[tuple[int, str]]) -> list[str]:
    """A section's list items (with their continuation lines) and paragraphs, as single-line strings."""
    out: list[list[str]] = []
    for _, line in lines:
        if not line.strip():
            out.append([])
        elif BULLET.match(line) or not out or not out[-1]:
            out.append([line.strip()])
        else:
            out[-1].append(line.strip())
    return [" ".join(u) for u in out if u]


def claim_units(kind: str, lines: list[tuple[int, str]]) -> list[str]:
    """The units of a current-state section that count as claims: a dated unit is history; Remaining work counts
    only its newest unit (the latest date when they are dated, else the last)."""
    all_units = units(lines)
    if kind == "remaining":
        dated = [(DATED.match(u).group(1), i) for i, u in enumerate(all_units) if DATED.match(u)]
        if dated:
            return [all_units[max(dated)[1]]]
        return all_units[-1:]
    return [u for u in all_units if not DATED.match(u)]


def state_after(text: str, start: int, stop: int, first_key: bool) -> str:
    """The state a key's own clause states: the first state word between the key and the next key (cut at the first
    sentence end); for the first key of a unit, the words just before it when its clause says none."""
    clause = text[start:stop]
    end = SENTENCE_END.search(clause)
    clause = clause[:end.start()] if end else clause
    windows = [clause] + ([text[max(0, start - 25):start]] if first_key else [])
    for window in windows:
        for m in STATE_WORD.finditer(window):
            before = window[:m.start()].split()[-1:] or [""]
            if before[0].lower().strip(",()") in NEGATING:
                continue
            word = m.group(1).lower()
            return "merged" if word == "merged" else "open" if word in ("open", "opened", "in review", "draft") else "closed"
    return ""


def unit_claims(doc: str, unit: str) -> list[Claim]:
    spans = []
    for pat in (URL_KEY, SHORT_KEY):
        spans += [(m.start(), m.end(), f"{m.group(1)}/{m.group(2)}", int(m.group(3))) for m in pat.finditer(unit)]
    spans.sort()
    claims = []
    for i, (s, e, repo, num) in enumerate(spans):
        stop = spans[i + 1][0] if i + 1 < len(spans) else len(unit)
        state = state_after(unit, e, stop, first_key=i == 0)
        if state:
            claims.append(Claim(doc, repo, num, state))
    return claims


def doc_claims(doc: Doc) -> list[Claim]:
    if doc.history:
        return []
    found: list[Claim] = []
    for kind, lines in sections(doc.text).items():
        for unit in claim_units(kind, lines):
            found += unit_claims(doc.rel, unit)
    return found


def default_runner(gh: str) -> Runner:
    def run(args: list[str]) -> tuple[int, str, str]:
        try:
            r = subprocess.run([*shlex.split(gh), *args], capture_output=True, text=True, timeout=GH_TIMEOUT,
                               env=kit_profile.gh_env())
        except (OSError, subprocess.SubprocessError) as e:
            return 127, "", str(e)
        return r.returncode, r.stdout, r.stderr
    return run


def graphql_query(repo: str, numbers: list[int]) -> str:
    owner, name = repo.split("/", 1)
    body = " ".join(f"n{n}: issueOrPullRequest(number: {n}) {{ __typename ... on Issue {{ state }} "
                    f"... on PullRequest {{ state }} }}" for n in numbers)
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {body} }} }}'


def read_states(repo: str, numbers: list[int], run: Runner) -> dict[int, str] | None:
    """{number: OPEN | CLOSED | MERGED} for the numbers GitHub knows, in ONE GraphQL query; None when the read
    failed (no usable `data.repository`). A number it does not know is simply absent from the dict."""
    rc, out, _ = run(["api", "graphql", "-f", f"query={graphql_query(repo, numbers)}"])
    try:
        repository = (json.loads(out).get("data") or {}).get("repository")
    except (ValueError, AttributeError):
        repository = None
    if not isinstance(repository, dict):
        return None
    states = {}
    for n in numbers:
        node = repository.get(f"n{n}")
        if isinstance(node, dict) and node.get("state") in ("OPEN", "CLOSED", "MERGED"):
            states[n] = node["state"]
    return states


def contradicts(said: str, actual: str) -> bool:
    return (said == "open" and actual != "OPEN") or (said == "merged" and actual != "MERGED") \
        or (said == "closed" and actual == "OPEN")


def check_claims(docs: list[Doc], run: Runner | None, max_keys: int, report: Report) -> None:
    """STALE where the system of record disagrees, UNCHECKED where it could not be read (offline, failed read, past
    the cap, unknown number) — a claim is never guessed."""
    claims = sorted((c for d in docs for c in doc_claims(d)), key=lambda c: (c.doc, c.repo, c.number, c.state))
    keys: list[tuple[str, int]] = []
    for c in claims:
        if (c.repo, c.number) not in keys:
            keys.append((c.repo, c.number))
    read, capped = keys[:max_keys], set(keys[max_keys:])
    actual: dict[tuple[str, int], str] = {}
    failed: dict[str, str] = {}
    for repo in sorted({r for r, _ in read}):
        numbers = [n for r, n in read if r == repo]
        if run is None:
            failed[repo] = "offline"
            continue
        states = read_states(repo, numbers, run)
        if states is None:
            failed[repo] = "gh read failed"
            report.gh_failed = True
            print(f"ctx_lint: gh read failed for {repo}", file=sys.stderr)
            continue
        actual.update({(repo, n): s for n, s in states.items()})
    for c in claims:
        key = (c.repo, c.number)
        said = f"{c.key} stated {c.state}"
        if key in capped:
            report.add("UNCHECKED", f"{c.doc} {said} (over the {max_keys}-key cap)")
        elif c.repo in failed:
            report.add("UNCHECKED", f"{c.doc} {said} ({failed[c.repo]})")
        elif key not in actual:
            report.add("UNCHECKED", f"{c.doc} {said} (not found on GitHub)")
        elif contradicts(c.state, actual[key]):
            report.add("STALE", f"{c.doc} {said} · github {c.key} is {actual[key]}")


def run_check(root: Path, run: Runner | None, max_keys: int, today: date) -> Report:
    docs, linkers = load_docs(root)
    report = Report()
    inbound = check_links(root, docs, linkers, report)
    check_orphans(docs, linkers, inbound, report)
    check_aged(docs, today, report)
    check_claims(docs, run, max_keys, report)
    return report


# ── the fork plan ────────────────────────────────────────────────────────────────────────────────

def read_stamp(path: Path) -> tuple[str, set[str]]:
    """(last lint date, domains the last run did not read) from the lint stamp; ("", ∅) without a usable one."""
    try:
        meta = fm.load_flat(path)
    except (OSError, UnicodeDecodeError):
        meta = None
    if not meta or not re.match(r"\d{4}-\d{2}-\d{2}", meta.get("last_lint", "")):
        return "", set()
    return meta["last_lint"][:10], {d.strip() for d in meta.get("not_read", "").strip("[]").split(",") if d.strip()}


def bundle_docs(docs: list[Doc]) -> dict[str, list[Doc]]:
    """The docs a fork reads, by domain: live knowledge, not history (logs, archive/, archived docs)."""
    by_domain: dict[str, list[Doc]] = {}
    for d in docs:
        if not d.history:
            by_domain.setdefault(d.domain, []).append(d)
    return by_domain


def render_bundle(domain_docs: list[Doc]) -> str:
    return "".join(f"=== {d.rel}\n{d.text.rstrip()}\n\n" for d in sorted(domain_docs, key=lambda d: d.rel))


def plan(docs: list[Doc], since: str, unread: set[str], max_forks: int, sonnet_over: int) -> list[str]:
    changed = []
    for domain, members in bundle_docs(docs).items():
        newest = max((d.meta.get("updated", "") for d in members), default="")
        if not since or domain in unread or newest >= since:
            changed.append((newest, domain, len(render_bundle(members).encode("utf-8"))))
    changed.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out = []
    for i, (_, domain, size) in enumerate(changed):
        if i < max_forks:
            out.append(f"FORK {domain} {'sonnet' if size > sonnet_over else 'haiku'} {size}")
        else:
            out.append(f"NOT READ {domain} {size}")
    return out or [f"NOTHING CHANGED since {since}"]


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────

def stamp_file(root: Path) -> Path:
    return root / "kit-health" / f"lint-{kit_profile.name()}.md"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Read-only lint over the context store: orphans, dead links, tracker "
                                 "states the store misstates, docs past the archive rule; plan and feed the "
                                 "judgement forks (docs/knowledge-lint.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("check", "mechanical findings, one per line, or CLEAN"),
                           ("plan", "which domains to fork since the last lint stamp"),
                           ("bundle", "one domain's docs as the fork's input")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--root", help="the content root (default: the active store)")
        if name == "check":
            p.add_argument("--offline", action="store_true", default=os.environ.get("CTX_LINT_OFFLINE") == "1",
                           help="never call gh: every tracker claim is UNCHECKED (env CTX_LINT_OFFLINE=1)")
            p.add_argument("--gh", default=os.environ.get("CTX_LINT_GH", "gh"), help="the gh command (env CTX_LINT_GH)")
            p.add_argument("--max-keys", type=int, default=MAX_KEYS, help=f"distinct tracker keys read per run ({MAX_KEYS})")
            p.add_argument("--today", help="YYYY-MM-DD, the day the archive rule counts from (default: today)")
        if name == "plan":
            p.add_argument("--stamp-file", help="the lint stamp (default: kit-health/lint-<env>.md under the root)")
            p.add_argument("--since", help="YYYY-MM-DD, instead of the stamp")
            p.add_argument("--max-forks", type=int, default=MAX_FORKS, help=f"forks per run ({MAX_FORKS})")
            p.add_argument("--sonnet-over", type=int, default=SONNET_OVER, help=f"bundle bytes past which a domain gets sonnet ({SONNET_OVER})")
        if name == "bundle":
            p.add_argument("domain")
    return ap


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    a = parser().parse_args(argv)
    root = Path(a.root) if a.root else kit_profile.context_root()
    if not root.is_dir():
        print(f"ctx_lint: no content root at {root}", file=sys.stderr)
        return 2
    try:
        if a.cmd == "check":
            today = date.fromisoformat(a.today) if a.today else date.today()
            run = None if a.offline else (runner or default_runner(a.gh))
            report = run_check(root.resolve(), run, a.max_keys, today)
            lines = report.render()
            print("\n".join(lines) if lines else "CLEAN")
            return 3 if report.findings() else 1 if report.gh_failed else 0
        docs, _ = load_docs(root.resolve())
        if a.cmd == "plan":
            since, unread = (a.since, set()) if a.since else read_stamp(Path(a.stamp_file) if a.stamp_file else stamp_file(root))
            if since:
                date.fromisoformat(since[:10])
            print("\n".join(plan(docs, since[:10], unread, a.max_forks, a.sonnet_over)))
            return 0
        members = bundle_docs(docs).get(a.domain)
        if not members:
            print(f"ctx_lint: no live docs in domain {a.domain}", file=sys.stderr)
            return 2
        print(render_bundle(members), end="")
        return 0
    except ValueError as e:
        print(f"ctx_lint: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
