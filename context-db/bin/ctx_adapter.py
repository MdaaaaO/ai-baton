#!/usr/bin/env python3
"""ctx_adapter.py — the kit's one adapter to ctx-store, the context-store CLI `ctx`.

The kit talks to ctx through its verbs only: it never reads or writes the store's own files (`ctx-store.json`,
`.ctx/`, `.audit/`), and `CTX_STORE` is an opaque locator it passes through, never a path it inspects. Whether a
store exists is ctx's answer (`NO_STORE`), not a file test here.

Pin — `CTX_VERSION` below is the one place the kit names the ctx-store release it is written against. It is
fetched, not vendored: `install` clones exactly that tag (`git clone --depth 1 --branch <tag>`) into a per-user
cache directory whose path carries the tag, so a bumped pin never picks up an older copy.

Resolver — `$KIT_CTX` (a ctx executable; set but unusable means "not installed", never a silent fallback), else the
pinned install `${XDG_CACHE_HOME:-~/.cache}/ai-baton-kit/ctx-store/<tag>/ctx`, else not installed.

Hooks — `hook <name>` is what `hooks/hooks.json` (plugin) and `settings.json` (clone) run, with Claude Code's hook
JSON on stdin. Every hook is a silent no-op (exit 0, no output) when ctx is not installed, when no store is named
or found, or when anything in the adapter itself fails, so a machine that has not adopted ctx-store sees nothing:

  post-tool-use        a Write/Edit under the content root → `ctx validate --changed --adopt`; a finding (exit 3)
                       comes back as `{"systemMessage": …}`; any exit but 0 or
                       NO_STORE (or a timeout) comes back as `ctx validate did not run: …`
  post-tool-use-async  the same trigger → `ctx touch --session <session_id>` (the registry row `session register`
                       stamped with the harness session id); output ignored
  brief-registry       SessionStart startup|resume|clear → `ctx brief --registry`, byte-budgeted
  brief-session        SessionStart compact → `ctx brief --session <session_id>`, byte-budgeted

The store a hook names: `CTX_STORE` when set (ctx reads it itself), else `--store <content root>`
(kit_profile.context_root()) — a write always names its store.

  python3 ctx_adapter.py version          # the pinned tag
  python3 ctx_adapter.py where            # the ctx executable; exit 1 when not installed
  python3 ctx_adapter.py install          # fetch the pinned tag into the pinned location (no-op when present)
  python3 ctx_adapter.py hook <name>      # one of the hooks above; hook JSON on stdin

Exit codes: 0 ok · 1 not installed · 2 usage or I/O error (one stderr line). A hook always exits 0. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CTX_VERSION = "v0.2.0"  # the ctx-store release tag the kit's adapters are written against — bump here only
CTX_REPO = "https://github.com/MdaaaaO/ctx-store"
BRIEF_BUDGET = 2048     # bytes of a SessionStart brief (ctx's default is 4096; the start of a session is prime context)
HOOK_TIMEOUT = 8        # seconds one ctx call may take inside a hook (the hook entries allow 10)
LOCK_TIMEOUT = "3"      # CTX_LOCK_TIMEOUT for a hook's ctx call unless the user set one: a held lock must not stall a tool
MESSAGE_MAX = 1000      # characters of validate findings returned as the systemMessage
CONTEXT_TOOLS = ("file_path", "notebook_path")  # the tool_input fields that name the file a Write/Edit/NotebookEdit changed


def pinned_dir(version: str = CTX_VERSION) -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "ai-baton-kit" / "ctx-store" / version


def _usable(p: Path) -> bool:
    return p.is_file() and os.access(p, os.X_OK)


def resolve() -> tuple[Path | None, str]:
    """(the ctx executable, "") or (None, why not) — `$KIT_CTX`, else the pinned install."""
    override = os.environ.get("KIT_CTX", "").strip()
    if override:
        p = Path(override).expanduser()
        return (p, "") if _usable(p) else (None, f"KIT_CTX={override} is not an executable file")
    p = pinned_dir() / "ctx"
    if _usable(p):
        return p, ""
    return None, (f"ctx-store {CTX_VERSION} is not installed ({p}) — run `python3 {Path(__file__).name} install`, "
                  "or set KIT_CTX to a ctx executable")


def install(url: str = CTX_REPO, version: str = CTX_VERSION, dest: Path | None = None) -> Path:
    """Clone `version` of `url` into `dest` (default: the pinned location) and check it reports that version. The clone
    lands in a temporary sibling first and is renamed into place, so an interrupted fetch never leaves a half copy
    the resolver would take. Already present: returns it untouched."""
    dest = dest or pinned_dir(version)
    if _usable(dest / "ctx"):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=dest.parent))
    try:
        src = tmp / "ctx-store"
        r = subprocess.run(["git", "-c", "advice.detachedHead=false", "clone", "-q", "--depth", "1", "--branch", version,
                            url, str(src)], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(f"git clone of {version} failed: {(r.stderr.strip().splitlines() or ['no output'])[-1]}")
        if not _usable(src / "ctx"):
            raise OSError(f"{version} of {url} has no executable `ctx`")
        v = subprocess.run([str(src / "ctx"), "--version"], capture_output=True, text=True, timeout=30)
        want = version.lstrip("v")
        if v.returncode != 0 or want not in v.stdout.split():
            raise OSError(f"the fetched ctx reports {v.stdout.strip() or v.stderr.strip()!r}, not {want}")
        shutil.rmtree(src / ".git", ignore_errors=True)  # a pinned copy, not a checkout anyone should commit in
        try:
            os.replace(src, dest)
        except OSError:
            if not _usable(dest / "ctx"):  # a concurrent install won the rename: theirs is as good as ours
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


# ── hooks ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _payload() -> dict:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _context_root() -> Path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import kit_profile  # same dir; imported only once ctx is known to be installed
    return kit_profile.context_root()


def _store_args(root: Path) -> list[str] | None:
    """The store a hook's call names: nothing when CTX_STORE is set (ctx reads it), else `--store <content root>`;
    None when there is neither (no content root on disk) — the hook then does nothing."""
    if os.environ.get("CTX_STORE", "").strip():
        return []
    return ["--store", str(root)] if root.is_dir() else None


def _ctx(ctx: Path, store: list[str], *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("CTX_LOCK_TIMEOUT", LOCK_TIMEOUT)
    return subprocess.run([str(ctx), *store, *args], env=env, capture_output=True, text=True, timeout=HOOK_TIMEOUT,
                          stdin=subprocess.DEVNULL)


def _changed_under(payload: dict, root: Path) -> bool:
    """True when the tool call changed a file under the content root."""
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return False
    base = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    real_root = os.path.realpath(root)
    for field in CONTEXT_TOOLS:
        f = ti.get(field)
        if isinstance(f, str) and f:
            p = os.path.realpath(os.path.join(base, os.path.expanduser(f)))
            if os.path.commonpath([p, real_root]) == real_root:
                return True
    return False


def _session_id(payload: dict) -> str:
    sid = payload.get("session_id")
    return sid.strip() if isinstance(sid, str) else ""


def hook(name: str) -> str:
    """What the hook prints (maybe nothing). Raises nothing the caller must handle beyond Exception."""
    payload = _payload()
    ctx, _ = resolve()
    if ctx is None:
        return ""
    root = _context_root()
    store = _store_args(root)
    if store is None:
        return ""
    if name in ("post-tool-use", "post-tool-use-async"):
        if not _changed_under(payload, root):
            return ""
        if name == "post-tool-use":
            try:
                r = _ctx(ctx, store, "validate", "--changed", "--adopt")
            except subprocess.TimeoutExpired:
                return json.dumps({"systemMessage": f"ctx validate did not run: no answer within {HOOK_TIMEOUT}s"})
            lines = [ln.strip() for ln in r.stderr.splitlines() if ln.strip()]
            if r.returncode == 3:  # validation findings
                return json.dumps({"systemMessage": f"ctx validate: {' · '.join(lines)[:MESSAGE_MAX]}"})
            if r.returncode == 0 or (lines and lines[0].split()[0] == "NO_STORE"):
                return ""  # clean, or the content root is not a store yet (not adopted): nothing to say
            # Any other exit (usage, lock timeout, read-only, a ctx that changed its verbs) means validation did
            # not happen; staying silent would read exactly like "no findings".
            first = lines[0] if lines else f"exit {r.returncode}"
            return json.dumps({"systemMessage": f"ctx validate did not run: {first[:MESSAGE_MAX]}"})
        sid = _session_id(payload)
        if sid:
            _ctx(ctx, store, "touch", "--session", sid)
        return ""
    if name == "brief-registry":
        r = _ctx(ctx, store, "brief", "--registry", "--budget", str(BRIEF_BUDGET))
    elif name == "brief-session":
        sid = _session_id(payload)
        if not sid:
            return ""
        r = _ctx(ctx, store, "brief", "--session", sid, "--budget", str(BRIEF_BUDGET))
    else:
        return ""
    return r.stdout if r.returncode == 0 else ""


HOOKS = ("post-tool-use", "post-tool-use-async", "brief-registry", "brief-session")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ctx_adapter.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version", help="print the pinned ctx-store tag")
    sub.add_parser("where", help="print the ctx executable; exit 1 when not installed")
    sub.add_parser("install", help="fetch the pinned tag into the pinned location")
    hp = sub.add_parser("hook", help="run one Claude Code hook (hook JSON on stdin); always exit 0")
    hp.add_argument("name", choices=HOOKS)
    a = p.parse_args(argv)
    if a.cmd == "hook":
        try:
            out = hook(a.name)
        except BaseException:  # noqa: BLE001 — a hook never fails the session: no output, exit 0
            return 0
        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
        return 0
    if a.cmd == "version":
        print(CTX_VERSION)
        return 0
    if a.cmd == "where":
        ctx, why = resolve()
        if ctx is None:
            print(why, file=sys.stderr)
            return 1
        print(ctx)
        return 0
    try:
        print(install())
    except (OSError, subprocess.SubprocessError) as e:
        print(f"ctx_adapter.py install: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
