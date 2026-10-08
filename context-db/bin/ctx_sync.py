#!/usr/bin/env python3
"""ctx_sync.py — keep a context store that is its own git repository in step with its remote (#515).

A `.context/` that is its own git work tree (its toplevel IS the content root, not a parent repo) can be shared
between machines and cloud sessions through a private remote. This script is the one place the kit touches that
repository: it commits the store's changes, pulls the remote with a rebase, merges what both sides changed where
that is safe, and pushes. Docs are written by ctx; this never edits a doc except through a merge driver.

Mode — the env config's `context.sync` (`kit_profile.py get context.sync`, default `auto`):
  auto     push when the store is its own work tree with an `origin` remote; commit only when it has no remote;
           nothing when it is not a repository of its own (a store inside a bigger repo is that repo's business)
  off      never touch git
  commit   commit, never push (also what `push` degrades to without a remote or on a detached HEAD)
  push     commit, pull --rebase, push

Subcommands:
  run [--no-push] [--quiet] [--timeout S] [--lock-wait S]
                          one sync pass; prints one line (`--quiet`: not when it is `clean …` or `off …` — with
                          `--no-push`, `clean` is a pull that brought and committed nothing); exit 0, 3 on a
                          conflict that needs a person, 2 on a git failure that is not a conflict
  status                                          one line: mode, branch, ahead/behind, a pending conflict
  merge-doc  O A B P      git merge driver for `*.md`: three-way frontmatter (per key; `updated` = newest; the side
                          with the newer `updated` wins a key both changed), bodies union-merged (`git merge-file
                          --union`: both sides' lines kept, nothing dropped)
  merge-max  O A B        merge driver for `.audit/seq`: the larger counter
  merge-regen O A B P     merge driver for a generated catalog: keep one side, regenerate after the rebase

How a pass runs (under `.git/ctx-sync.lock`, so two sessions on one machine never race git's index.lock; `.git`
may be a gitfile — `--separate-git-dir`, a worktree — and the lock, status and record live in the git dir it names):
  0. a rebase left mid-way by a pass a hook timeout killed (status `running`, nobody holds the lock) is aborted
     first; a rebase a person started by hand stops the pass (exit 3) until they finish or abort it.
  1. `git add -A` + commit when the store changed (`--no-verify`; author = the registered session name when the
     repository has no identity of its own).
  2. fetch `origin/<branch>`; when behind, `git rebase --autostash` with the merge drivers above active
     (`.gitattributes` + repo-local `merge.ctx-*` config, (re)written on every pass — a plugin install's path
     changes per release). A rebase that stops is aborted: the local commit stays, the conflicting paths go to
     `.git/ctx-sync/status`, exit 3 — the hooks show it until a pass succeeds.
  3. the docs the drivers merged go through `ctx validate --changed --adopt`; a finding undoes the rebase
     (`reset --hard ORIG_HEAD`, autostash preserved), same status + exit 3. Catalogs are regenerated and
     committed when a merge touched them.
  4. push (`HEAD:<branch>`); a non-fast-forward rejection retries the fetch/rebase/push twice; a network failure
     leaves the commit for the next pass (`committed …, push pending`). `GIT_TERMINAL_PROMPT=0`: a missing
     credential fails fast, never hangs a hook.

Callers: ctx_adapter.py's `post-tool-use-async` hook (after every context write), `brief-registry` (pull before the
session-start brief, `--no-push`), session.py (register / touch / end / rename), `make -C $BATON/context-db context-sync`,
kit-health. Stdlib only. Design and conflict table: docs/context-sync.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402  — same dir

MODES = ("auto", "off", "commit", "push")
GENERATED = ("INDEX.md", "SESSION_INDEX.md", "sessions/archive/INDEX.md")
ATTR_BEGIN = "# ai-baton context sync (ctx_sync.py) — begin"
ATTR_END = "# ai-baton context sync — end"
ATTR_LINES = (
    "*.md merge=ctx-doc",                # first: the specific rules below win over it (last match wins)
    "sessions/_ledger.md merge=union",
    ".audit/*.jsonl merge=union",
    ".audit/seq merge=ctx-max",
    *(f"{g} merge=ctx-regen" for g in GENERATED),
)
DRIVERS = {  # merge.<name>.driver — %O base %A ours %B theirs %P path; written as this script's absolute path
    "ctx-doc": ("ai-baton context doc: frontmatter three-way, body union", "merge-doc %O %A %B %P"),
    "ctx-max": ("ai-baton audit counter: the larger", "merge-max %O %A %B"),
    "ctx-regen": ("ai-baton generated catalog: keep one side, regenerate", "merge-regen %O %A %B %P"),
}
NET_TIMEOUT = 25        # seconds a fetch or push may take in `run` unless --timeout says otherwise
LOCK_WAIT = 15          # seconds `run` waits for another pass on the same store
PUSH_RETRIES = 2        # non-fast-forward rejections retried (someone pushed between our fetch and push)
SUMMARY_PATHS = 3       # paths named in a commit subject


class Git:
    """git against the store, with the environment a pass needs."""

    def __init__(self, root: Path, timeout: float = NET_TIMEOUT):
        self.root = root
        self.timeout = timeout
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0", CTX_SYNC=str(Path(__file__).resolve()))
        self.env.pop("GIT_DIR", None)
        self.env.pop("GIT_WORK_TREE", None)

    def run(self, *args: str, timeout: float | None = None, check: bool = False) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", "-C", str(self.root), *args], env=self.env, capture_output=True, text=True,
                           timeout=self.timeout if timeout is None else timeout, stdin=subprocess.DEVNULL)
        if check and r.returncode != 0:
            raise GitError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip().splitlines()[-1:] or ['exit ' + str(r.returncode)]}"[0:300])
        return r

    def out(self, *args: str, timeout: float | None = None) -> str:
        r = self.run(*args, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""


class GitError(Exception):
    pass


# ── mode ──────────────────────────────────────────────────────────────────────────────────────────────────────
def configured_mode() -> str:
    if not (kit_profile.ENV_DIR / "config.json").is_file():
        return "auto"  # no env store yet (env-init's starting point): the default, and no stderr line from a hook
    v = str(kit_profile.get("context.sync", "auto") or "auto").strip().lower()
    return v if v in MODES else "auto"


def git_dir(root: Path) -> Path:
    """The store's git directory: `.git` itself, or where a `.git` *file* (`--separate-git-dir`, a worktree) points."""
    p = root / ".git"
    if p.is_file():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("gitdir:"):
                    target = Path(line[7:].strip())
                    return target if target.is_absolute() else (root / target).resolve()
        except OSError:
            pass
    return p


def rebase_in_progress(root: Path) -> bool:
    d = git_dir(root)
    return (d / "rebase-merge").exists() or (d / "rebase-apply").exists()


def own_toplevel(git: Git) -> bool:
    """True when the content root is the top of its own work tree (not a directory inside a bigger repo); `.git` may
    be a directory or a gitfile."""
    if not (git.root / ".git").exists():
        return False
    top = git.out("rev-parse", "--show-toplevel", timeout=10)
    try:
        return bool(top) and Path(top).resolve() == git.root.resolve()
    except OSError:
        return False


def mode_of(git: Git) -> tuple[str, str]:
    """(effective mode, why) — `off` with the reason, `commit`, or `push`."""
    want = configured_mode()
    if want == "off":
        return "off", "context.sync: off"
    if not own_toplevel(git):
        return "off", "the store is not its own git repository"
    branch = git.out("symbolic-ref", "--short", "HEAD", timeout=10)
    remote = git.out("remote", "get-url", "origin", timeout=10)
    if want == "commit":
        return "commit", "context.sync: commit"
    if not remote:
        return "commit", "no origin remote"
    if not branch:
        return "commit", "detached HEAD"
    return "push", f"origin/{branch}"


# ── status file ───────────────────────────────────────────────────────────────────────────────────────────────
def status_path(root: Path) -> Path:
    return git_dir(root) / "ctx-sync" / "status"


def read_status(root: Path) -> dict:
    try:
        d = json.loads(status_path(root).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(root: Path, state: str, detail: str = "", paths: list[str] | None = None) -> None:
    p = status_path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"state": state, "detail": detail, "paths": paths or [],
                                 "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n",
                     encoding="utf-8")
    except OSError:
        pass


def pending_conflict(root: Path) -> str:
    """One line for a hook to show while a conflict (or a rebase someone started by hand) waits for a person, else ''.
    A rebase a killed pass left behind (status `running`) is not reported: the next pass recovers it."""
    st = read_status(root)
    if rebase_in_progress(root) and st.get("state") not in ("running", "conflict"):
        return ("context sync: a rebase is in progress in the store — finish it (`git rebase --continue`) or "
                "`git rebase --abort`, then `make -C $BATON/context-db context-sync`")
    if st.get("state") != "conflict":  # a conflict with a rebase still in progress: its detail says what failed
        return ""
    paths = ", ".join(st.get("paths") or [])[:200]
    return (f"context sync: conflict pending{' in ' + paths if paths else ''} — {st.get('detail', '')[:200]}; "
            f"resolve in the store, then `make -C $BATON/context-db context-sync`")


# ── attributes + drivers ──────────────────────────────────────────────────────────────────────────────────────
def ensure_drivers(git: Git) -> None:
    """The managed block of `.gitattributes` (tracked, so every clone merges the same way) and the repo-local
    driver config pointing at THIS script's path (rewritten when it moved — a plugin install moves per release)."""
    attrs = git.root / ".gitattributes"
    try:
        text = attrs.read_text(encoding="utf-8") if attrs.is_file() else ""
    except OSError:
        text = ""
    block = "\n".join([ATTR_BEGIN, *ATTR_LINES, ATTR_END]) + "\n"
    if ATTR_BEGIN in text and ATTR_END in text:
        head, rest = text.split(ATTR_BEGIN, 1)
        _, tail = rest.split(ATTR_END, 1)
        new = head + block.rstrip("\n") + tail
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    if new != text:
        attrs.write_text(new, encoding="utf-8")
    me = str(Path(__file__).resolve())
    for name, (desc, tail) in DRIVERS.items():
        cmd = f'{json.dumps(sys.executable)} {json.dumps(me)} {tail}'
        if git.out("config", "--local", f"merge.{name}.driver", timeout=10) != cmd:
            git.run("config", "--local", f"merge.{name}.name", desc, timeout=10)
            git.run("config", "--local", f"merge.{name}.driver", cmd, timeout=10)


# ── the pass ──────────────────────────────────────────────────────────────────────────────────────────────────
def actor() -> str:
    for v in (kit_profile.session_name(), os.environ.get("CTX_ACTOR", ""), kit_profile.identity("WORKSPACE_USER")):
        if v and v.strip():
            return v.strip()
    try:
        import getpass
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "ctx-sync"


def identity_env(git: Git) -> dict[str, str]:
    """Author/committer for the commit when the repository (and the user's global config) name none."""
    if git.out("config", "user.name", timeout=10) and git.out("config", "user.email", timeout=10):
        return {}
    who = actor()
    login = kit_profile.identity("WORKSPACE_GITHUB_LOGIN")
    mail = f"{login}@users.noreply.github.com" if login else f"{who}@ctx-sync.invalid"
    return {"GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": mail, "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": mail}


def subject(paths: list[str], who: str) -> str:
    docs = [p for p in paths if not p.startswith(".audit/") and p not in GENERATED and p != ".gitattributes"]
    if docs:
        named = ", ".join(docs[:SUMMARY_PATHS]) + (f" +{len(docs) - SUMMARY_PATHS}" if len(docs) > SUMMARY_PATHS else "")
    else:
        named = "catalogs and audit"
    return f"context: {named} ({who})"[:72]


def commit_changes(git: Git) -> str:
    """Stage and commit everything under the store; returns the short sha, or '' when nothing changed."""
    git.run("add", "-A", "--", ".", timeout=60, check=True)
    if git.run("diff", "--cached", "--quiet", timeout=60).returncode == 0:
        return ""
    paths = git.out("diff", "--cached", "--name-only", timeout=60).splitlines()
    env = identity_env(git)
    saved = git.env
    git.env = dict(saved, **env)
    try:
        git.run("commit", "-q", "--no-verify", "-m", subject(paths, actor()), timeout=60, check=True)
    finally:
        git.env = saved
    return git.out("rev-parse", "--short", "HEAD", timeout=10)


def merged_record(root: Path) -> Path:
    return git_dir(root) / "ctx-sync" / "merged"


def read_merged(root: Path) -> list[str]:
    try:
        return [x for x in merged_record(root).read_text(encoding="utf-8").splitlines() if x]
    except OSError:
        return []


def validate_merged(root: Path, paths: list[str]) -> str:
    """`ctx validate --changed --adopt` after a merge touched docs; '' when clean or when ctx is not here."""
    if not any(p.endswith(".md") and p not in GENERATED for p in paths):
        return ""
    try:
        import ctx_adapter  # same dir
    except ImportError:
        return ""
    ctx, _ = ctx_adapter.resolve()
    if ctx is None:
        return ""
    try:
        r = ctx_adapter._ctx(ctx, ["--store", str(root)], "validate", "--changed", "--adopt", timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return f"ctx validate did not run: {e}"
    if r.returncode == 3:
        lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
        return "ctx validate: " + " · ".join(lines)[:400]
    return ""


def regenerate(root: Path) -> None:
    env = dict(os.environ, CONTEXT_ROOT=str(root))
    for args in (["gen_index.py"], ["gen_sessions.py", "--no-archive"]):
        try:
            subprocess.run([sys.executable, str(BIN / args[0]), *args[1:]], env=env, cwd=BIN, capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass


def rebase_onto_remote(git: Git, branch: str) -> tuple[bool, str]:
    """fetch + rebase. (True, '') when up to date or rebased clean; (False, why) on a conflict — the rebase is
    already undone and the status file written."""
    root = git.root
    try:
        git.run("fetch", "-q", "origin", branch, check=True)
    except (GitError, subprocess.TimeoutExpired) as e:
        raise NoNetwork(str(e)) from e
    if not git.out("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}", timeout=10):
        return True, ""  # nothing on the remote yet
    behind = git.out("rev-list", "--count", f"HEAD..origin/{branch}", timeout=30)
    if behind in ("", "0"):
        return True, ""
    try:
        merged_record(root).parent.mkdir(parents=True, exist_ok=True)
        merged_record(root).write_text("", encoding="utf-8")
    except OSError:
        pass
    write_status(root, "running", "rebase")  # a pass killed here (a hook timeout) is recognised and undone by the next one
    r = git.run("rebase", "-q", "--autostash", f"origin/{branch}", timeout=120)
    if r.returncode != 0:
        conflicted = git.out("diff", "--name-only", "--diff-filter=U", timeout=30).splitlines()
        git.run("rebase", "--abort", timeout=60)
        why = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["rebase stopped"]
        write_status(root, "conflict", why[0][:200], conflicted)
        return False, f"conflict: {', '.join(conflicted) or why[0][:120]}"
    write_status(root, "ok")
    merged = read_merged(root)
    finding = validate_merged(root, merged)
    if finding:
        dirty = git.out("status", "--porcelain", "--", ".", timeout=30)
        if dirty:
            git.run("stash", "-q", "--include-untracked", timeout=60)
        git.run("reset", "-q", "--hard", "ORIG_HEAD", timeout=60)
        if dirty:
            git.run("stash", "pop", "-q", timeout=60)
        write_status(root, "conflict", finding, [p for p in merged if p.endswith(".md")])
        return False, f"conflict: {finding}"
    if merged:
        regenerate(root)
        commit_changes(git)  # the regenerated catalogs, when they differ
    return True, ""


class NoNetwork(Exception):
    pass


def acquire(root: Path, wait: float):
    """The store's sync lock (`<git dir>/ctx-sync.lock`), or None when another pass held it for `wait` seconds."""
    p = git_dir(root) / "ctx-sync.lock"
    fh = open(p, "a", encoding="utf-8")  # noqa: SIM115 — held for the pass
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.monotonic() >= deadline:
                fh.close()
                return None
            time.sleep(0.2)


def run(root: Path, push: bool = True, timeout: float = NET_TIMEOUT, lock_wait: float = LOCK_WAIT) -> tuple[int, str]:
    """One pass. (exit code, the one line)."""
    git = Git(root, timeout)
    if configured_mode() == "off" or not own_toplevel(git):
        mode, why = mode_of(git)
        return 0, f"off ({why})"
    lock = acquire(root, lock_wait)
    if lock is None:
        return 0, "busy (another sync pass holds the lock; its `git add -A` sweeps this change up)"
    try:
        if rebase_in_progress(root):  # before mode_of: a rebase detaches HEAD, which would read as "commit only"
            if read_status(root).get("state") != "running":
                return 3, "conflict: a rebase is in progress in the store — finish or `git rebase --abort` it, then rerun"
            # a pass killed mid-rebase (a hook timeout): nobody drives that rebase — the lock is ours — so undo it
            # (`--autostash` restores what it stashed) and go on; this pass fetches and rebases again
            r = git.run("rebase", "--abort", timeout=60)
            if r.returncode != 0 or rebase_in_progress(root):
                why = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["rebase --abort failed"]
                write_status(root, "conflict", f"could not undo an interrupted rebase: {why[0][:160]}")
                return 3, ("conflict: could not undo the rebase an interrupted pass left — in the store, `git rebase --abort`, "
                           "or remove `.git/rebase-merge` when git cannot read it (`git status` says), then rerun")
            write_status(root, "ok")
        mode, why = mode_of(git)
        if mode == "off":
            return 0, f"off ({why})"
        ensure_drivers(git)
        sha = commit_changes(git)
        if mode == "commit":
            return 0, f"committed {sha} ({why}, no push)" if sha else f"clean ({why}, no push)"
        branch = why.split("/", 1)[1]
        head = git.out("rev-parse", "HEAD", timeout=10)
        try:
            ok, detail = rebase_onto_remote(git, branch)
        except NoNetwork as e:
            ahead = git.out("rev-list", "--count", f"origin/{branch}..HEAD", timeout=30) or "?"
            return 0, f"{'committed ' + sha + ', ' if sha else ''}push pending ({ahead} ahead; fetch failed: {str(e)[:120]})"
        if not ok:
            return 3, detail
        if not push:
            write_status(root, "ok")
            ahead = git.out("rev-list", "--count", f"origin/{branch}..HEAD", timeout=30) or "0"
            if not sha and git.out("rev-parse", "HEAD", timeout=10) == head:
                return 0, (f"clean (in sync with origin/{branch})" if ahead == "0"
                           else f"clean (nothing to pull; {ahead} ahead, push left to the next pass)")
            return 0, f"{'committed ' + sha + ', ' if sha else ''}pulled ({ahead} ahead, push left to the next pass)"
        for attempt in range(PUSH_RETRIES + 1):
            ahead = git.out("rev-list", "--count", f"origin/{branch}..HEAD", timeout=30) or "0"
            if ahead == "0":
                write_status(root, "ok")
                return 0, f"clean (in sync with origin/{branch})"
            try:
                r = git.run("push", "-q", "origin", f"HEAD:{branch}")
            except subprocess.TimeoutExpired:
                return 0, f"push pending ({ahead} ahead; push timed out after {int(timeout)}s)"
            if r.returncode == 0:
                write_status(root, "ok")
                return 0, f"pushed {git.out('rev-parse', '--short', 'HEAD', timeout=10)} ({ahead} commit{'s' if ahead != '1' else ''}) to origin/{branch}"
            err = (r.stderr or r.stdout).strip()
            if "rejected" in err and attempt < PUSH_RETRIES:
                try:
                    ok, detail = rebase_onto_remote(git, branch)
                except NoNetwork as e:
                    return 0, f"push pending ({ahead} ahead; fetch failed: {str(e)[:120]})"
                if not ok:
                    return 3, detail
                continue
            last = err.splitlines()[-1:] or ["push failed"]
            return 0, f"push pending ({ahead} ahead; {last[0][:160]})"
        return 0, "push pending (gave up after repeated non-fast-forward rejections)"
    except GitError as e:
        return 2, f"git failed: {e}"
    except subprocess.TimeoutExpired as e:
        return 0, f"push pending (git timed out: {str(e)[:120]})"
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        except OSError:
            pass


def status(root: Path) -> str:
    git = Git(root, 10)
    mode, why = mode_of(git)
    if mode == "off":
        return f"off ({why})"
    parts = [mode, why]
    dirty = len(git.out("status", "--porcelain", "--", ".", timeout=30).splitlines())
    if dirty:
        parts.append(f"{dirty} uncommitted")
    if mode == "push":
        branch = why.split("/", 1)[1]
        if git.out("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}", timeout=10):
            ahead = git.out("rev-list", "--count", f"origin/{branch}..HEAD", timeout=30) or "?"
            behind = git.out("rev-list", "--count", f"HEAD..origin/{branch}", timeout=30) or "?"
            parts.append(f"{ahead} ahead, {behind} behind (as of the last fetch)")
        else:
            parts.append("remote branch not fetched yet")
    if rebase_in_progress(root) and read_status(root).get("state") == "running":
        parts.append("a pass was interrupted mid-rebase (the next pass undoes it)")
    pending = pending_conflict(root)
    if pending:
        parts.append(pending)
    return "; ".join(parts)


# ── merge drivers ─────────────────────────────────────────────────────────────────────────────────────────────
def _read(p: str) -> str:
    try:
        return Path(p).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def split_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """(the lines between the `---` fences, the rest) — (None, text) when the file carries no frontmatter."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, text
    return text[4:end].split("\n"), text[end + 5:]


def _fm_map(lines: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in lines or []:
        if ":" in ln and not ln.startswith((" ", "\t", "#")):
            k, v = ln.split(":", 1)
            out.setdefault(k.strip(), v.strip())
    return out


def merge_frontmatter(base: list[str] | None, ours: list[str] | None, theirs: list[str] | None) -> list[str]:
    """Per key: the side that changed it wins; both changed → `updated` the newest, any other key the side whose
    `updated` is newer (theirs on a tie — in a rebase that is the commit being replayed, this machine's own edit).
    Key order: ours, then theirs' additions."""
    b, o, t = _fm_map(base), _fm_map(ours), _fm_map(theirs)
    prefer_theirs = t.get("updated", "") >= o.get("updated", "")
    out: dict[str, str] = {}
    for k in [*o, *[k for k in t if k not in o]]:
        ov, tv = o.get(k), t.get(k)
        if ov is None or tv is None:
            v = ov if tv is None else tv
            if k in b and b[k] == v:  # one side deleted it: the deletion wins
                continue
        elif ov == tv:
            v = ov
        elif k == "updated":
            v = max(ov, tv)
        elif ov == b.get(k):
            v = tv
        elif tv == b.get(k):
            v = ov
        else:
            v = tv if prefer_theirs else ov
        out[k] = v
    return [f"{k}: {v}" for k, v in out.items()]


def _record(path: str) -> None:
    """Note the path for the pass that drives this rebase (the record lives beside the store's .git)."""
    try:
        top = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=10).stdout.strip()
        if top:
            rec = Path(top) / "ctx-sync" / "merged"
            rec.parent.mkdir(parents=True, exist_ok=True)
            with open(rec, "a", encoding="utf-8") as f:
                f.write(path + "\n")
    except (OSError, subprocess.SubprocessError):
        pass


def merge_doc(base: str, ours: str, theirs: str, path: str) -> int:
    fb, bb = split_frontmatter(_read(base))
    fo, bo = split_frontmatter(_read(ours))
    ft, bt = split_frontmatter(_read(theirs))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        files = []
        for name, body in (("ours", bo), ("base", bb), ("theirs", bt)):
            p = Path(td) / name
            p.write_text(body, encoding="utf-8")
            files.append(str(p))
        r = subprocess.run(["git", "merge-file", "-p", "--union", *files], capture_output=True, text=True, timeout=60)
    if r.returncode < 0 or r.returncode > 127:
        return 1
    body = r.stdout
    if fo is None and ft is None:
        text = body
    else:
        text = "---\n" + "\n".join(merge_frontmatter(fb, fo, ft)) + "\n---\n" + body
    Path(ours).write_text(text, encoding="utf-8")
    _record(path)
    return 0


def merge_max(base: str, ours: str, theirs: str) -> int:
    try:
        n = max(int(_read(ours).strip() or 0), int(_read(theirs).strip() or 0))
    except ValueError:
        return 1
    Path(ours).write_text(f"{n}\n", encoding="utf-8")
    return 0


def merge_regen(base: str, ours: str, theirs: str, path: str) -> int:
    _record(path)
    return 0  # keep ours; the pass regenerates the catalog after the rebase


# ── cli ───────────────────────────────────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ctx_sync.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("run", help="one sync pass: commit, pull --rebase, push")
    rp.add_argument("--no-push", action="store_true", help="commit and pull only (the session-start hook)")
    rp.add_argument("--quiet", action="store_true", help="print nothing when the pass was a no-op (`clean`/`off`)")
    rp.add_argument("--timeout", type=float, default=NET_TIMEOUT, help=f"seconds for one fetch or push (default {NET_TIMEOUT})")
    rp.add_argument("--lock-wait", type=float, default=LOCK_WAIT, help=f"seconds to wait for another pass (default {LOCK_WAIT})")
    sub.add_parser("status", help="one line: mode, branch, ahead/behind, pending conflict")
    for name, n in (("merge-doc", 4), ("merge-max", 3), ("merge-regen", 4)):
        mp = sub.add_parser(name, help=f"git merge driver ({n} arguments: base ours theirs{' path' if n == 4 else ''})")
        mp.add_argument("args", nargs=n)
    a = p.parse_args(argv)
    if a.cmd == "merge-doc":
        return merge_doc(*a.args)
    if a.cmd == "merge-max":
        return merge_max(*a.args)
    if a.cmd == "merge-regen":
        return merge_regen(*a.args)
    root = kit_profile.context_root()
    if not root.is_dir():
        print(f"off (no content root at {root})")
        return 0
    if a.cmd == "status":
        print(status(root))
        return 0
    code, line = run(root, push=not a.no_push, timeout=a.timeout, lock_wait=a.lock_wait)
    if not (a.quiet and line.startswith(("clean", "off"))):
        print(f"context sync: {line}" if a.cmd == "run" else line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
