#!/usr/bin/env python3
"""signq — the sign-queue tool (host side: `make sign`; session side: enqueue.sh calls `meta`).

    signq.py run   [-v] [--dry-run]     take the drain lock, migrate any legacy jobs (not on a dry run),
                                        then drain every pending job, non-interactive, with an overview.
                                        A lock already held prints its holder's pid and current job, then
                                        exits 0. A lock just freed by a holder that died mid-job (its job
                                        still running) prints that job's name + pid and exits 2 without
                                        starting anything.
    signq.py list                       overview table of pending + parked jobs (no git runs)
    signq.py show  <job>                print a job's metadata and the script itself
    signq.py log   <job>                print the last drain log of a job
    signq.py retry <job>                un-park a <job>.failed so the next `run` picks it up
    signq.py drop  <job>                delete a pending or parked job (its log is kept)
    signq.py meta  <wt> <branch> <msg> [--topic T] [--by S] [--ticket K] [--epic K] [--pr N] [--summary S]
                                        emit the META JSON line enqueue.sh embeds in a job
    signq.py migrate-legacy             move jobs/logs a pre-workspace-queue kit queued under the kit dir
                                        into the workspace queue and print what moved; a no-op otherwise.
                                        Takes the drain lock for the move; while a drain holds it, nothing
                                        moves here (`run` migrates itself, under its lock, before it loads
                                        a job). `make sign_list` runs it first (`-q`: silent unless files move).

<job> is the 1-based index from `list`, the topic, or the file name. Jobs are self-contained POSIX sh
scripts under .context/state/sign-queue/ (written by enqueue.sh). A job carries one `# META {...}` line with
ticket / epic / repo / PR / subject; legacy jobs without it are parsed from their header + worktree.

Everything git might make interactive is disabled for the drain: GIT_PAGER=cat (no `q`), GIT_EDITOR=true
and GIT_SEQUENCE_EDITOR=true (no `:wq`), GIT_TERMINAL_PROMPT=0. Per-job output goes to
.context/state/sign-queue/logs/<job>.log; the terminal only gets the milestones (or everything with -v).
Stdlib only; runs on the host's system python3 (3.9+).
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

KIT = Path(__file__).resolve().parents[2]  # skills/sign-queue → the kit (a .claude/ clone or the plugin root)
sys.path.insert(0, str(KIT / "context-db" / "bin"))
import kit_profile  # noqa: E402

# The queue lives in the workspace's `.context/state/sign-queue/` (#7): under the kit it sat in the plugin cache on a
# plugin install, which an update deletes. SIGN_QUEUE_ROOT / SIGN_QUEUE_CONTEXT / SIGN_QUEUE_DIR still override.
if os.environ.get("SIGN_QUEUE_ROOT"):
    ROOT = Path(os.environ["SIGN_QUEUE_ROOT"])
    CONTEXT = Path(os.environ.get("SIGN_QUEUE_CONTEXT", str(ROOT / ".context")))
else:
    CONTEXT = Path(os.environ.get("SIGN_QUEUE_CONTEXT", str(kit_profile.context_root())))
    ROOT = CONTEXT.parent
Q = Path(os.environ.get("SIGN_QUEUE_DIR", str(CONTEXT / "state" / "sign-queue")))
LEGACY_Q = KIT / "sign-queue"  # where jobs were queued before #7
LOGS = Q / "logs"
LOCK = Q / ".lock"  # held exclusively (fcntl.flock) for the whole drain. Its bytes also record the holder's
# pid and the job it is currently on (_write_lock_state/_read_lock_state): a second `run` that finds the
# lock held names both; a `run` that takes a just-freed lock reads the same bytes for a job process a dead
# holder left running (SIGKILL, …) and refuses to start a new one on top of it.

# ── terminal styling ────────────────────────────────────────────────────────────────────────
TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if TTY else s


def bold(s: str) -> str: return _c("1", s)
def dim(s: str) -> str: return _c("2", s)
def green(s: str) -> str: return _c("32", s)
def red(s: str) -> str: return _c("31", s)
def yellow(s: str) -> str: return _c("33", s)
def cyan(s: str) -> str: return _c("36", s)


def width() -> int:
    return shutil.get_terminal_size((120, 40)).columns


def trunc(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(n - 1, 1)] + "…"


def now_z() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── shell helpers ───────────────────────────────────────────────────────────────────────────
def sh(args: List[str], cwd: Optional[str] = None, timeout: int = 15, env: Optional[dict] = None) -> Tuple[bool, str, str]:
    """Run a command, return (ok, stdout, reason). ok is False on a non-zero exit, a timeout or a launch
    error (missing binary, …); reason is then a short one-line explanation (never the full stderr dump) —
    a caller that only looked at stdout used to see '' and could not tell "ran clean, said nothing" apart
    from "failed outright". Never raises."""
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "", f"timed out after {timeout}s"
    except OSError as e:
        return False, "", f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        line = next((ln.strip() for ln in r.stderr.splitlines() if ln.strip()), f"exit {r.returncode}")
        return False, "", line[:160]
    return True, r.stdout.strip(), ""


def gh_env() -> dict:
    # github.sandbox_token_prefix from the env store (a placeholder token where a sandbox proxy injects the
    # real one, empty where gh is logged in natively) — the same helper every kit script uses
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "context-db" / "bin"))
    try:
        import kit_profile
        return kit_profile.gh_env()
    except Exception as e:  # no kit_profile, unreadable/malformed store: gh with the plain environment
        print(f"signq: env store unreadable ({type(e).__name__}: {e}) — gh runs without "
              "github.sandbox_token_prefix", file=sys.stderr)
        return dict(os.environ)


# ── metadata derivation (shared by enqueue.sh via `meta` and by the drain for legacy jobs) ──
def repo_of(wt: str) -> Dict[str, object]:
    """{"repo": "<owner>/<repo>", "repo_lookup_error": ""} — or repo="" with a reason when `git remote
    get-url` itself failed (no origin configured reads as repo="" with no error: that is not a failure)."""
    ok, out, reason = sh(["git", "-C", wt, "remote", "get-url", "origin"])
    if not ok:
        return {"repo": "", "repo_lookup_error": reason}
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", out)
    return {"repo": m.group(1) if m else "", "repo_lookup_error": ""}


def key_regex() -> Optional["re.Pattern[str]"]:
    """This environment's ticket-key shape, from the env config's `tracker.key_regex` (the same source
    `context-db/bin/commit_style.py`'s `key_regex()` reads) — never a hardcoded tracker's shape. No
    configured regex (or one that fails to compile) means no key matching at all, not a fallback shape."""
    rx = str(kit_profile.get("tracker.key_regex") or "")
    if not rx:
        return None
    try:
        return re.compile(rx)
    except re.error:
        return None


def ticket_of(*candidates: str) -> str:
    """The first key found in `candidates`: the regex's capture group when it has one (the key without the
    characters the regex only anchors on), else the whole match."""
    rx = key_regex()
    if rx is None:
        return ""
    for c in candidates:
        if not c:
            continue
        m = rx.search(c) or rx.search(c.upper())
        if m:
            return (m.group(1) if rx.groups and m.group(1) else m.group(0)).upper()
    return ""


def epic_of(ticket: str) -> Dict[str, str]:
    """Find the epic a ticket belongs to via the .context/ epic docs (type: epic) that mention it.
    The epic key comes from the doc's slug/title (e.g. key-123-<slug>.md → KEY-123)."""
    if not ticket or not CONTEXT.is_dir():
        return {}
    best, best_hits = None, 0
    for p in CONTEXT.rglob("*.md"):
        if "archive" in p.parts or p.name in ("INDEX.md", "README.md"):
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        fm = text.split("---", 2)[1] if text.count("---") >= 2 else ""
        if not re.search(r"^type:\s*epic\s*$", fm, re.M):
            continue
        key = ticket_of(p.stem, re.search(r"^title:\s*(.*)$", fm, re.M).group(1) if re.search(r"^title:", fm, re.M) else "")
        if not key:
            continue
        # a bare issue number counts only where it is written as a reference (`#12`), never inside `0.12` or `112`
        lead = r"(?<![\w/])#" if ticket.isdigit() else r"(?<!\w)"
        hits = len(re.findall(lead + re.escape(ticket) + r"\b", text))
        if key == ticket:
            hits += 1000  # the ticket IS the epic
        if hits > best_hits:
            best, best_hits = (key, p, fm), hits
    if not best:
        return {}
    key, p, fm = best
    title = ""
    m = re.search(r"^title:\s*(.*)$", fm, re.M)
    if m:
        title = m.group(1).strip().strip('"').strip("'")
        title = re.sub(r"^" + re.escape(key) + r"\s*[—:\-–]?\s*", "", title)
    return {"epic": key, "epic_title": title, "epic_doc": str(p.relative_to(CONTEXT))}


def pr_of(repo: str, branch: str) -> Dict[str, object]:
    """The open PR for repo/branch. A failed lookup (gh missing, gh error, timeout, bad JSON) is NOT "no PR":
    it returns {"pr_lookup_error": <reason>} so the queue shows the failure instead. A clean "no open PR" returns
    {"pr_lookup_error": ""} (not {}) on purpose: callers meta.update() the result, and the empty value clears a
    stale error from an earlier lookup. {} only when there is nothing to look up (no repo or branch).
    No `gh` on PATH is not a failure but a documented skip: {"pr_lookup_skipped": "gh not on PATH"} — the column
    then reads `no gh → pass --pr`, and `--pr <n>` fills it by hand."""
    if not repo or not branch:
        return {}
    if not shutil.which("gh"):
        return {"pr_lookup_error": "", "pr_lookup_skipped": "gh not on PATH"}
    try:
        r = subprocess.run(["gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "open",
                            "--json", "number,url,title,isDraft"], capture_output=True, text=True,
                           timeout=20, env=gh_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"pr_lookup_error": type(e).__name__, "pr_lookup_skipped": ""}
    if r.returncode != 0:
        why = next((ln.strip() for ln in r.stderr.splitlines() if ln.strip()), f"exit {r.returncode}")
        return {"pr_lookup_error": why[:120], "pr_lookup_skipped": ""}
    try:
        rows = json.loads(r.stdout) if r.stdout.strip() else []
    except json.JSONDecodeError:
        return {"pr_lookup_error": "unparseable gh output", "pr_lookup_skipped": ""}
    if not rows:
        return {"pr_lookup_error": "", "pr_lookup_skipped": ""}
    row = rows[0]
    return {"pr": row.get("number"), "pr_url": row.get("url"), "pr_title": row.get("title"),
            "pr_draft": bool(row.get("isDraft")), "pr_lookup_error": "", "pr_lookup_skipped": ""}


def subject_of(msg: str) -> str:
    try:
        for line in Path(msg).read_text(errors="replace").splitlines():
            if line.strip():
                return line.strip()
    except OSError:
        pass
    return ""


def build_meta(wt: str, branch: str, msg: str, topic: str = "", by: str = "", ticket: str = "", epic: str = "",
               pr: str = "", summary: str = "", flags: Optional[List[str]] = None, files: int = -1) -> Dict[str, object]:
    repo_info = repo_of(wt)
    repo = str(repo_info.get("repo") or "")
    subject = summary or subject_of(msg)
    ticket = ticket.upper() if ticket else ticket_of(branch, topic, subject)
    meta: Dict[str, object] = {
        "v": 1, "topic": topic, "by": by, "enqueued": now_z(),
        "wt": wt, "branch": branch, "msg": msg,
        "repo": repo, "ticket": ticket, "subject": subject,
        "repo_lookup_error": repo_info.get("repo_lookup_error") or "",
        "flags": flags or [], "files": files,
    }
    if epic:
        meta["epic"] = epic.upper()
    else:
        meta.update(epic_of(ticket))
    if pr and pr.strip().lower() == "none":  # "no PR yet" (a first push); callers have always passed it
        pr = ""
    if pr:
        if not re.match(r"^\d+$", pr):
            raise ValueError(f"--pr expects a bare PR number, got {pr!r}")
        meta["pr"] = int(pr)
        if repo:
            meta["pr_url"] = f"https://github.com/{repo}/pull/{meta['pr']}"
    else:
        meta.update(pr_of(repo, branch))
    return meta


# ── job files ───────────────────────────────────────────────────────────────────────────────
class Job:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name[:-len(".failed")] if path.name.endswith(".failed") else path.name
        self.parked = path.name.endswith(".failed")
        self.text = path.read_text(errors="replace")
        self.meta = self._parse()

    @property
    def topic(self) -> str:
        # the timestamp prefix, optionally followed by the same-second collision suffix enqueue.sh adds
        # (`-NNN-`, always three digits) — a legacy job from before it has no suffix to strip, and a
        # topic that itself starts with a number (`46-fix`) keeps it.
        return str(self.meta.get("topic") or re.sub(r"^\d{8}T\d{6}Z-(?:\d{3}-)?", "", self.name)[:-3])

    @property
    def log(self) -> Path:
        return LOGS / (self.name + ".log")

    def _parse(self) -> Dict[str, object]:
        m = re.search(r"^# META (\{.*\})\s*$", self.text, re.M)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # legacy job: header lines + variables, derive the rest from the worktree (best effort)
        meta: Dict[str, object] = {"legacy": True}
        h = re.search(r"^# sign-queue job: (\S+)\s+\(enqueued (\S+) by session (.+?)\)\s*$", self.text, re.M)
        if h:
            meta.update(topic=h.group(1), enqueued=h.group(2), by=h.group(3))
        v = self._assignments()
        if all(k in v for k in ("WT", "BR", "MSG")):
            wt, br, msg = v["WT"], v["BR"], v["MSG"]
            meta.update(build_meta(wt, br, msg, topic=str(meta.get("topic", "")), by=str(meta.get("by", "")),
                                   flags=self._flags_from_script(), files=self._files_from_script()))
            meta["enqueued"] = h.group(2) if h else ""
        return meta

    def _assignments(self) -> Dict[str, str]:
        """The job's `NAME='value'` lines as sh reads them: one per line since #123 (values quoted by enqueue.sh's
        sq()), or the older `WT='…'; BR='…'; MSG='…'` one-liner."""
        out: Dict[str, str] = {}
        for line in self.text.splitlines():
            if not re.match(r"(WT|BR|MSG|UP|OLD_BASE|LEASE)=", line):
                continue
            try:
                lex = shlex.shlex(line, posix=True, punctuation_chars=";")  # `;` outside quotes is its own token
                lex.whitespace_split = True
                words = list(lex)
            except ValueError:
                continue
            out.update(w.split("=", 1) for w in words if "=" in w)
        return out

    def _flags_from_script(self) -> List[str]:
        f = []
        if "rebase -S FETCH_HEAD" in self.text: f.append("rebase")
        if "rebase -S --onto" in self.text: f.append("onto")
        if "push -u origin" in self.text: f.append("new-branch")
        if "--force-with-lease" in self.text: f.append("force-with-lease")
        if re.search(r'^git -C "\$WT" add -- ', self.text, re.M): f.append("files")
        return f

    def _files_from_script(self) -> int:
        m = re.search(r'^git -C "\$WT" add -- (.*)$', self.text, re.M)
        if not m:
            return -1
        try:
            return len(shlex.split(m.group(1)))
        except ValueError:
            return -1

    # display helpers
    def g(self, k: str, default: str = "") -> str:
        v = self.meta.get(k)
        return default if v in (None, "", [], -1) else str(v)

    def repo_short(self) -> str:
        return self.g("repo").split("/")[-1]

    def pr_label(self) -> str:
        pr = self.g("pr")
        if not pr:
            err = self.g("pr_lookup_error")
            if err:
                return f"PR lookup failed ({trunc(err, 40)})"
            if self.g("pr_lookup_skipped"):
                return "no gh → pass --pr"
            return "new branch → no PR yet" if "new-branch" in self.meta.get("flags", []) else "—"
        return f"#{pr}" + (" (draft)" if self.meta.get("pr_draft") else "")

    def flags_label(self) -> str:
        fl = list(self.meta.get("flags", []))
        n = self.meta.get("files", -1)
        if "files" in fl and isinstance(n, int) and n >= 0:
            fl[fl.index("files")] = f"{n} file{'s' if n != 1 else ''}"
        return " ".join(fl) if fl else "all files"


def load_jobs() -> List[Job]:
    jobs = [Job(p) for p in sorted(Q.glob("*.sh"))]
    jobs += [Job(p) for p in sorted(Q.glob("*.sh.failed"))]
    return jobs


def pick(jobs: List[Job], ref: str) -> Job:
    if ref.isdigit() and 1 <= int(ref) <= len(jobs):
        return jobs[int(ref) - 1]
    for j in jobs:
        if ref in (j.name, j.path.name, j.topic, j.name[:-3]):
            return j
    hits = [j for j in jobs if ref in j.name or ref in j.topic]
    if len(hits) == 1:
        return hits[0]
    sys.exit(f"no unique job matches {ref!r} (use the index from `list`)")


# ── rendering ───────────────────────────────────────────────────────────────────────────────
def table(jobs: List[Job]) -> str:
    if not jobs:
        return dim("  (queue empty)")
    cols = ["#", "TICKET", "EPIC", "REPO", "PR", "BRANCH", "COMMIT", "STAGE", "BY"]
    rows = []
    for i, j in enumerate(jobs, 1):
        rows.append([
            f"{i}" + ("✘" if j.parked else ""), j.g("ticket", "—"), j.g("epic", "—"), j.repo_short() or "—",
            j.pr_label(), j.g("branch", "—"), j.g("subject", "—"), j.flags_label(), j.g("by", "?"),
        ])
    # fixed widths for everything but COMMIT, which absorbs the rest of the terminal
    w = [max(len(c), *(len(r[k]) for r in rows)) for k, c in enumerate(cols)]
    w[5] = min(w[5], 34)
    fixed = sum(w) - w[6] + 2 * (len(cols) - 1) + 2
    w[6] = max(24, min(w[6], width() - fixed))
    out = [dim("  " + "  ".join(c.ljust(w[k]) for k, c in enumerate(cols)))]
    for r in rows:
        cells = [trunc(x, w[k]).ljust(w[k]) for k, x in enumerate(r)]
        line = "  " + "  ".join(cells)
        out.append(red(line) if r[0].endswith("✘") else line)
    return "\n".join(out)


def header(jobs: List[Job]) -> str:
    pend = sum(1 for j in jobs if not j.parked)
    park = len(jobs) - pend
    parts = [bold("sign queue"), f"{pend} pending"]
    if park:
        parts.append(red(f"{park} parked"))
    return "  ".join(parts) + dim(f"   {now_z()}   {Q}")


def job_card(i: int, n: int, j: Job) -> str:
    epic = j.g("epic")
    et = j.g("epic_title")
    who = " · ".join(x for x in [
        j.g("ticket"), (f"{epic} {dim(et)}" if et else epic) if epic else "",
        f"{j.repo_short()} {j.pr_label()}" if j.repo_short() else "",
    ] if x)
    steps = ["stage " + j.flags_label(), "commit -S"]
    fl = j.meta.get("flags", [])
    if "rebase" in fl: steps.append("rebase -S onto origin/" + j.g("branch"))
    if "onto" in fl: steps.append("rebase -S --onto upstream")
    if "new-branch" in fl: steps.append("push -u")
    elif "force-with-lease" in fl: steps.append("push --force-with-lease")
    else: steps.append("push")
    return (f"\n{bold(f'[{i}/{n}]')} {who}\n"
            f"      {cyan(j.g('subject', j.topic))}\n"
            f"      {dim(j.g('branch'))}  {dim('·')}  {dim(' → '.join(steps))}  {dim('· by ' + j.g('by', '?'))}")


# ── running ─────────────────────────────────────────────────────────────────────────────────
MILESTONES = [
    (re.compile(r"^\[[^\]]+ (?:\(root-commit\) )?([0-9a-f]{7,})\] (.*)"), lambda m: f"committed {m.group(1)}  {m.group(2)}"),
    (re.compile(r"^\(already committed\)"), lambda m: "already committed (push-only retry)"),
    (re.compile(r"^Successfully rebased"), lambda m: "rebased onto the remote tip"),
    (re.compile(r"^\s*([0-9a-f]{7,})\.\.([0-9a-f]{7,})\s+(\S+) -> "), lambda m: f"pushed {m.group(1)}..{m.group(2)}"),
    (re.compile(r"^\s*\* \[new branch\]\s+(\S+) -> "), lambda m: f"pushed new branch {m.group(1)}"),
    (re.compile(r"^\s*\+ [0-9a-f]+\.\.\.[0-9a-f]+ .*\(forced update\)"), lambda m: "force-pushed (lease held)"),
    (re.compile(r"^pushed ([0-9a-f]+) (\S) (.*)"), None),  # final line, handled separately
]
FAIL_HINTS = [
    (re.compile(r"^UNSIGNED "), "a commit below HEAD would have pushed unsigned — the job refused the push "
     "before it landed; a sandbox-made commit surviving a no-op re-stack is the usual cause"),
    # anchored to git's own push-rejection line (`! [rejected]        branch -> branch (…)`) — a bare
    # "rejected" also matches a commit subject that happens to contain the word (e.g. a fix for rejected
    # payments), which used to trigger this hint on an unrelated failure.
    (re.compile(r"non-fast-forward|fetch first|! \[rejected\]"), "remote moved: re-enqueue with --rebase (or --force-with-lease after a rewrite)"),
    (re.compile(r"CONFLICT|could not apply"), "rebase conflict: the owning session resolves in the worktree, then re-enqueues"),
    (re.compile(r"gpg failed to sign|signing failed|No secret key|Couldn't load public key|failed to write commit object"),
     "signing failed: the signing key is not available (a drain only works on the host)"),
    (re.compile(r"stale info|lease"), "lease broken: the remote tip is not the one the session inspected"),
    (re.compile(r"unbound variable"), "job script bug: re-enqueue (enqueue.sh quotes paths since 2026-09-19)"),
    (re.compile(r"Permission denied \(publickey\)|Could not read from remote"), "ssh auth to GitHub failed on the host"),
    (re.compile(r"nothing to commit|no changes added"), "nothing staged: worktree already committed?"),
]
SIG = {"G": "signed ✓", "U": "signed, untrusted key", "B": "BAD signature", "N": "UNSIGNED", "E": "sig unverifiable",
       "X": "expired sig", "Y": "expired key", "R": "revoked key"}


def _descendants(pid: int) -> List[int]:
    """Every process descended from pid, BFS one generation at a time — `pgrep -P <ppid>` is the
    portable way to ask (POSIX-ish; ships with Linux's procps and with macOS out of the box), unlike
    walking /proc (Linux-only, absent on macOS/BSD). A `pgrep` that finds nothing exits non-zero with
    empty stdout, same as "no children" — never treated as an error here."""
    out: List[int] = []
    frontier = [pid]
    while frontier:
        nxt: List[int] = []
        for p in frontier:
            nxt.extend(_children(p))
        out.extend(nxt)
        frontier = nxt
    return out


def _children(ppid: int) -> List[int]:
    """Direct children of ppid: `pgrep -P` where it exists, else /proc's stat files (a minimal image
    with no procps), else none — the caller still kills the root, so a missing tool never leaves the
    job itself running."""
    try:
        r = subprocess.run(["pgrep", "-P", str(ppid)], capture_output=True, text=True, timeout=10)
        return [int(x) for x in r.stdout.split()]
    except (OSError, subprocess.SubprocessError):
        pass
    kids: List[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return kids
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            # field 4 is the ppid; the command name (field 2) may hold spaces, so split after its ')'
            if int(stat.rsplit(")", 1)[1].split()[1]) == ppid:
                kids.append(int(d.name))
        except (OSError, ValueError, IndexError):
            continue
    return kids


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_start(pid: int) -> str:
    """The process's start time as `ps -o lstart=` reports it, or "" when `ps` has nothing to say (no such
    pid, or a `ps` without `lstart` — some minimal images). The same probe sync.sh's `pid_start()` uses for
    its own lock-staleness check, so a pid later reused by an unrelated process is never mistaken for the
    one that originally held it."""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip()


def _job_still_alive(pid: int, started: str) -> bool:
    """Mirrors sync.sh's `owner_gone()`: alive by `os.kill(pid, 0)`, and — when a start time was recorded —
    still the SAME process by `ps -o lstart=` (a pid reused by an unrelated process after the original
    exited reads as gone, not alive). No recorded start time, or `ps` unable to answer right now, falls
    back to the plain liveness check: never treat a live pid as gone just because the extra check could not
    be made."""
    if not _alive(pid):
        return False
    if not started:
        return True
    now = _pid_start(pid)
    return not now or now == started


def _read_lock_state(lock_f) -> Dict[str, object]:
    """The lock file's recorded state: {"pid": the holder's os.getpid(), "job": the job name it is
    currently on ("" when idle), "job_pid": that job's own process id (or None), "job_started": that
    process's `_pid_start()` string}. A fresh lock file, or one from before this recording existed, reads
    as {} — never an error."""
    try:
        lock_f.seek(0)
        text = lock_f.read()
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _write_lock_state(lock_f, holder_pid: Optional[int] = None, job: str = "", job_pid: Optional[int] = None,
                      job_started: str = "") -> None:
    """Overwrite the lock file with the drain's current state — called once right after the lock is taken,
    again each time the drain moves onto a new job (and once more to clear the job fields when that job
    finishes), so the bytes always reflect what the holder is doing right now. A second `run` that finds
    the lock held reads this to name the holder; a `run` that takes a just-freed lock reads it to tell a
    leftover live job (the previous holder died mid-job) from a clean handoff."""
    lock_f.seek(0)
    lock_f.truncate()
    lock_f.write(json.dumps({"pid": os.getpid() if holder_pid is None else holder_pid, "job": job,
                             "job_pid": job_pid, "job_started": job_started}))
    lock_f.flush()


def _kill_tree(root: int, grace: float = 2.0) -> None:
    """SIGTERM, then (after `grace` seconds) SIGKILL, every process descended from `root` — a job's
    `sh <job>` and everything it forked (git, gh, a hung `sleep` two levels down) — children before
    the parent each pass, so a parent never disappears out from under a child pgrep might otherwise
    still need to place in the tree. Runs in the caller's own session and process group throughout
    (never `start_new_session` / `setpgrp` on the job's Popen): a job may need the controlling
    terminal for `ssh-keygen -Y sign` / `ssh` to prompt for a passphrase on /dev/tty, and a background
    process GROUP that tries to read the tty gets SIGTTIN, not a prompt — a hang worse than the one
    this is guarding against. `root` itself may already be gone by the time this runs; killing a pid
    that no longer exists is not an error."""
    # deepest descendants first, the root last — children before the parent, as described above
    tree = list(reversed(_descendants(root))) + [root]
    for pid in tree:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(_alive(pid) for pid in tree):
        time.sleep(0.1)
    for pid in tree:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_job(j: Job, verbose: bool, dry: bool, lock_f=None) -> Dict[str, str]:
    LOGS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(GIT_PAGER="cat", PAGER="cat", GIT_EDITOR="true", GIT_SEQUENCE_EDITOR="true",
               GIT_TERMINAL_PROMPT="0", GIT_MERGE_AUTOEDIT="no", GIT_ADVICE="0")
    result: Dict[str, str] = {"status": "skipped" if dry else "failed", "sha": "", "sig": "", "subject": ""}
    if dry:
        print(dim("      (dry run — not executed)"))
        return result
    start = _dt.datetime.now()
    # A job script chains git fetch/rebase/push (and sometimes gh); none of that has its own
    # timeout once it is inside "sh <job>". A hung remote or a credential prompt would otherwise
    # block this read loop forever. JOB_TIMEOUT bounds the whole job; _kill_tree takes down the
    # job's whole process tree (not just "sh <job>" itself — a grandchild left running would keep
    # the stdout pipe's write end open and the read below would never see EOF).
    JOB_TIMEOUT = int(os.environ.get("SIGN_QUEUE_JOB_TIMEOUT", "300"))
    timed_out = threading.Event()

    def _on_timeout(pid: int) -> None:
        timed_out.set()
        _kill_tree(pid)

    with j.log.open("a") as log:
        log.write(f"\n===== {now_z()} run {j.name}\n")
        p = subprocess.Popen(["sh", str(j.path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=env, bufsize=1, stdin=subprocess.DEVNULL)
        if lock_f is not None:
            # the job stays in this drain's session (see _kill_tree), so Ctrl-C stops both. A drain killed
            # outright still leaves the job running: the lock file names its process for the next `run`.
            _write_lock_state(lock_f, job=j.name, job_pid=p.pid, job_started=_pid_start(p.pid))
        timer = threading.Timer(JOB_TIMEOUT, _on_timeout, args=(p.pid,))
        timer.start()
        tail: List[str] = []
        try:
            assert p.stdout is not None
            for raw in p.stdout:
                line = raw.rstrip("\n")
                log.write(line + "\n")
                tail.append(line); tail = tail[-25:]
                if verbose:
                    # tee the raw line to the console, but still parse it below — the milestones
                    # and the final "pushed <sha> <sig> <subject>" marker are the only evidence a
                    # push actually happened, verbose or not.
                    print("      " + dim("│ ") + line)
                m = re.match(r"^pushed ([0-9a-f]+) (\S) (.*)", line)
                if m:
                    result.update(sha=m.group(1), sig=m.group(2), subject=m.group(3))
                    continue
                if not verbose:
                    for rx, fmt in MILESTONES:
                        mm = rx.match(line)
                        if mm and fmt:
                            print("      " + dim("·") + " " + fmt(mm))
                            break
            rc = p.wait()
        finally:
            timer.cancel()
            if p.stdout is not None:
                p.stdout.close()  # the tree is dead (or never existed): release the pipe, not just let it leak
        if timed_out.is_set():
            log.write(f"===== timed out after {JOB_TIMEOUT}s, killed\n")
        else:
            log.write(f"===== exit {rc}\n")
    secs = int((_dt.datetime.now() - start).total_seconds())
    # rc == 0 alone is not proof of a push: it only means the job script ran to its last line
    # without error. The job's own last command prints "pushed <sha> <sig> <subject>" (parsed
    # above); require that marker too, or a job that exits clean without ever reaching it (a
    # malformed or truncated script) would be recorded as pushed on rc alone.
    if not timed_out.is_set() and rc == 0 and result["sha"]:
        result["status"] = "pushed"
        sig = SIG.get(result["sig"], result["sig"])
        sigtxt = green(sig) if result["sig"] == "G" else yellow(sig)
        print(f"      {green('✔')} {bold('pushed ' + result['sha'])}  {sigtxt}  {dim(f'{secs}s')}")
    else:
        if timed_out.is_set():
            hint = f"job timed out after {JOB_TIMEOUT}s (a hung git/gh call?) — killed"
        elif rc == 0:
            hint = "job exited 0 but never printed its push confirmation — a truncated or edited job script?"
        else:
            hint = next((h for rx, h in FAIL_HINTS if any(rx.search(t) for t in tail)), "")
        print(f"      {red('✘ FAILED')} exit {rc}  {dim(f'{secs}s')}  → parked as {red(j.path.name + '.failed')}")
        if hint:
            print(f"      {yellow('hint:')} {hint}")
        print(dim("      last output:"))
        for t in [t for t in tail if t.strip()][-8:]:
            print(dim("      │ ") + trunc(t, width() - 10))
        print(dim(f"      full log: {j.log}"))
    return result


def resolve_pr_after_push(j: Job) -> None:
    """A --new-branch job has no PR at enqueue time; after the push the session opens one. Look once."""
    if j.g("pr") or not j.g("repo"):
        return
    j.meta.update(pr_of(j.g("repo"), j.g("branch")))


# a previous drain died (SIGKILL, …) while its job kept running; this `run` took the lock but refuses to
# start a second job on top of it.
# Non-zero on purpose: the queue is not drained and someone must look at the still-running job.
EXIT_STALE_JOB = 2


def cmd_run(argv: List[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv
    dry = "--dry-run" in argv
    # the exclusive lock guards the whole drain, acquired before anything else runs — including the
    # legacy migration and the job listing that follow — so a second concurrent `run` sees nothing but
    # one line naming the current holder (its pid and the job it is on), never a header or table it has
    # no business printing. LOCK_NB: never block, just say so and leave.
    Q.mkdir(parents=True, exist_ok=True)
    lock_f = open(LOCK, "a+")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        state = _read_lock_state(lock_f)
        holder = state.get("pid", "?")
        job = state.get("job") or "(no job yet)"
        print(dim(f"sign queue: another drain (pid {holder}, on {job}) already holds the lock — exiting"))
        lock_f.close()
        return 0
    try:
        # the lock is ours, but its bytes may still describe the PREVIOUS holder: a recorded job pid
        # that is still alive (and still the same process, by start time — PID reuse guard) means that
        # holder died mid-job rather than finishing cleanly, and its job runs on. Starting a new job on
        # top of it would be a second drain in practice, just serialised through a dead holder's lock.
        prior = _read_lock_state(lock_f)
        job_pid = prior.get("job_pid")
        if isinstance(job_pid, int) and _job_still_alive(job_pid, str(prior.get("job_started") or "")):
            job_name = str(prior.get("job") or "a job")
            print(red(f"sign queue: a previous drain died, but its job {job_name} (pid {job_pid}) is "
                      "still running — exiting without starting a job"))
            return EXIT_STALE_JOB
        _write_lock_state(lock_f)
        if not dry:
            migrate_legacy()  # under the lock, before any job loads; a dry run moves nothing
        return _drain(verbose, dry, lock_f)
    finally:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        lock_f.close()


def _drain(verbose: bool, dry: bool, lock_f=None) -> int:
    jobs = load_jobs()
    pending = [j for j in jobs if not j.parked]
    parked = [j for j in jobs if j.parked]
    print(header(jobs))
    if not pending:
        print(dim("  nothing to sign"))
        if parked:
            print(table(parked))
            print(dim("  parked jobs: `make sign_retry JOB=<n>` to re-run, `make sign_drop JOB=<n>` to remove"))
        return 0
    print(table(pending))
    results = []
    for i, j in enumerate(pending, 1):
        if not j.path.exists():
            # gone since `list`/`load_jobs` read it (a hand `rm`, or a `sign_drop` run alongside this
            # drain) — never let a missing file abort the loop and lose the summary for every other job.
            print(dim(f"  skipped {j.name} — job file disappeared mid-drain"))
            continue
        print(job_card(i, len(pending), j))
        r = run_job(j, verbose, dry, lock_f=lock_f)
        if lock_f is not None:
            _write_lock_state(lock_f)  # back to idle
        if r["status"] == "pushed":
            try:
                j.path.unlink()
            except FileNotFoundError:
                print(dim(f"  {j.name} pushed, but its job file was already gone"))
            resolve_pr_after_push(j)
        elif r["status"] == "failed":
            try:
                j.path.rename(j.path.with_name(j.path.name + ".failed"))
            except FileNotFoundError:
                print(dim(f"  {j.name} failed, but its job file was already gone — not parked"))
        results.append((j, r))
    if dry:
        return 0
    # summary
    ok = [(j, r) for j, r in results if r["status"] == "pushed"]
    bad = [(j, r) for j, r in results if r["status"] == "failed"]
    print("\n" + bold("summary") + f"  {green(str(len(ok)) + ' pushed')}" + (f"  {red(str(len(bad)) + ' failed')}" if bad else ""))
    for j, r in results:
        mark = green("✔") if r["status"] == "pushed" else red("✘")
        link = j.g("pr_url") or (f"https://github.com/{j.g('repo')}/tree/{j.g('branch')}" if j.g("repo") else "")
        tag = f"{j.g('ticket', '—')} · {j.repo_short()} {j.pr_label()}"
        print(f"  {mark} {tag:<40} {trunc(j.g('subject'), 60):<60} {dim(link)}")
    if parked:
        print(dim(f"\n  {len(parked)} older parked job(s) untouched — `make sign_list` shows them"))
    if bad:
        print(red("\n  failed jobs are parked; tell the owning session(s): " + ", ".join(f"{j.topic} ({j.g('by','?')})" for j, _ in bad)))
        return 1
    return 0


def cmd_list(_: List[str]) -> int:
    jobs = load_jobs()
    print(header(jobs))
    print(table(jobs))
    if any(j.parked for j in jobs):
        print(dim("  ✘ = parked failure (`make sign_retry JOB=<n>` / `make sign_drop JOB=<n>` / `make sign_log JOB=<n>`)"))
    return 0


def cmd_show(argv: List[str]) -> int:
    j = pick(load_jobs(), argv[0])
    print(bold(j.path.name) + ("  " + red("(parked)") if j.parked else ""))
    for k in ("ticket", "epic", "epic_title", "repo", "repo_lookup_error", "pr", "pr_url", "pr_lookup_error",
              "pr_lookup_skipped", "branch", "wt", "msg", "subject", "flags", "files", "by", "enqueued"):
        if j.meta.get(k) not in (None, "", [], -1):
            print(f"  {k:<15} {j.meta[k]}")
    print(dim("\n--- script ---"))
    print(j.text.rstrip())
    return 0


def cmd_log(argv: List[str]) -> int:
    j = pick(load_jobs(), argv[0])
    if not j.log.exists():
        print(f"no log yet for {j.name}")
        return 1
    print(j.log.read_text().rstrip())
    return 0


def cmd_retry(argv: List[str]) -> int:
    j = pick(load_jobs(), argv[0])
    if not j.parked:
        print(f"{j.name} is already pending")
        return 0
    j.path.rename(j.path.with_name(j.name))
    print(f"un-parked {j.name} — next `make sign` runs it")
    return 0


def cmd_drop(argv: List[str]) -> int:
    j = pick(load_jobs(), argv[0])
    j.path.unlink()
    print(f"dropped {j.path.name}" + (f" (log kept at {j.log})" if j.log.exists() else ""))
    return 0


def cmd_meta(argv: List[str]) -> int:
    if len(argv) < 3:
        sys.exit("usage: signq.py meta <wt> <branch> <msg> [--topic T] [--by S] [--ticket K] [--epic K] [--pr N] [--summary S] [--flags a,b] [--files N]")
    wt, br, msg = argv[:3]
    opts: Dict[str, str] = {}
    it = iter(argv[3:])
    for a in it:
        if a.startswith("--"):
            opts[a[2:]] = next(it, "")
    try:
        meta = build_meta(wt, br, msg, topic=opts.get("topic", ""), by=opts.get("by", ""), ticket=opts.get("ticket", ""),
                          epic=opts.get("epic", ""), pr=opts.get("pr", ""), summary=opts.get("summary", ""),
                          flags=[f for f in opts.get("flags", "").split(",") if f], files=int(opts.get("files", "-1") or -1))
    except ValueError as e:
        print(f"signq.py meta: {e}", file=sys.stderr)
        return 2
    print(json.dumps(meta, ensure_ascii=False))
    return 0


def migrate_legacy(legacy: Path = LEGACY_Q, q: Path = Q, *, holds_lock: bool = True) -> int:
    """Move jobs and logs a pre-workspace-queue kit queued under the kit dir into the workspace queue; returns
    how many files moved. Never overwrites a job already in the new queue (it stays in place and is named).
    Called from exactly two places, never as a side effect of `list`/`show`/`meta`: `run`, once it holds the
    drain lock and before it loads a job, and the `migrate-legacy` subcommand. The move is a queue write, so it
    always happens under the drain lock: `holds_lock=False` (the subcommand) takes the lock for the move and
    moves nothing while a drain holds it — that drain migrates itself."""
    if "SIGN_QUEUE_DIR" in os.environ or not legacy.is_dir() or legacy.resolve() == q.resolve():
        return 0
    if not holds_lock:
        q.mkdir(parents=True, exist_ok=True)
        with open(q / LOCK.name, "a+") as lock_f:
            try:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 0
            try:
                return migrate_legacy(legacy, q)
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    moved = 0
    for src in sorted([*legacy.glob("*.sh"), *legacy.glob("*.sh.failed"), *legacy.glob("logs/*.log")]):
        dst = q / src.relative_to(legacy)
        if dst.exists():
            print(f"sign-queue: {src} not moved — {dst} exists; resolve by hand", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1
    if moved:
        print(f"sign-queue: moved {moved} file(s) from {legacy} to {q} (#7)", file=sys.stderr)
    return moved


def cmd_migrate_legacy(args: List[str]) -> int:
    """`-q`: say something only when files moved (`make sign_list` runs it before every overview)."""
    moved = migrate_legacy(holds_lock=False)
    if moved:
        print(f"moved {moved} file(s) from {LEGACY_Q} to {Q}")
    elif "-q" not in args:
        print("nothing to migrate" if not LEGACY_Q.is_dir() else f"{LEGACY_Q} and {Q} already the same directory, or nothing new to move")
    return 0


def main(argv: List[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("run", [])
    if cmd in ("-v", "--verbose", "--dry-run"):
        cmd, rest = "run", argv
    if cmd == "--list":
        cmd = "list"
    table_ = {"run": cmd_run, "list": cmd_list, "show": cmd_show, "log": cmd_log, "retry": cmd_retry, "drop": cmd_drop,
              "meta": cmd_meta, "migrate-legacy": cmd_migrate_legacy}
    if cmd in ("-h", "--help", "help") or cmd not in table_:
        print(__doc__.strip())
        return 0 if cmd in ("-h", "--help", "help") else 2
    if cmd in ("show", "log", "retry", "drop") and not rest:
        sys.exit(f"usage: signq.py {cmd} <job>")
    return table_[cmd](rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
