#!/usr/bin/env python3
"""ctx_adapter.py — the kit's one adapter to ctx-store, the context-store CLI `ctx`.

The kit talks to ctx through its verbs only: it never reads or writes the store's own files (`ctx-store.json`,
`.ctx/`, `.audit/`), and `CTX_STORE` is an opaque locator it passes through, never a path it inspects. Whether a
store exists is ctx's answer (`NO_STORE`), not a file test here. The one exception is `adopt` (below).

Pin — `CTX_VERSION` below is the one place the kit names the ctx-store release it is written against. It is
fetched, not vendored: `install` clones exactly that tag (`git clone --depth 1 --branch <tag>`) into a per-user
cache directory whose path carries the tag, so a bumped pin never picks up an older copy.

Resolver — `$KIT_CTX` (a ctx executable; set but unusable means "not installed", never a silent fallback), else the
pinned install `${XDG_CACHE_HOME:-~/.cache}/ai-baton-kit/ctx-store/<tag>/ctx`, else not installed.

Adopt — makes the content root a store and keeps its settings and type schemas at the kit's: `ctx init` hands over
`context-db/ctx-store/` with `--upgrade` (idempotent: a store file still holding what the last `init` wrote takes the
kit's new copy; one someone edited is kept and reported, exit 5, until `adopt --replace` takes the kit's), then
`ctx migrate --apply` (the docs of a type the kit moved to a new schema version, e.g. the chronological Session
log), `ctx validate` (findings printed) and `ctx validate --changed --adopt` (records every doc as it is). `--check`
is the read-only probe kit-health runs. The kit never writes a store file itself — except `ctx-store.json`'s `mcp`
key (`_strip_mcp_for_upgrade`/`_apply_mcp_setting`, below): the pinned ctx's own `init` does not accept it in
`--settings` yet (ctx-store#66/#71), though every other ctx entry point reads it straight from the file. Every other
key follows `init`'s own kept rule above; `mcp` does not — a local edit to it is not supported, because the kit owns
this key (its `actors` pattern is `session.py`'s own NAME_RE — a local pattern would break session writes), so
`adopt` always replaces it with the kit's value, never silently: `replaced: ctx-store.json mcp — the kit owns this
key (session.py's name pattern); a local value is not kept` prints, and counts toward exit 5 same as a `differs:`
line, whenever the value replaced was not already the kit's; an equal value prints nothing. `--upgrade` decides
`kept` vs `written` by the marker's digest, not its content (ctx-store#73), so before it runs the adapter strips its
own last `mcp` patch off the marker (the digest then matches what `init` last wrote) and puts the kit's `mcp` value
back once `init` is done, reporting the swap as above. The marker is written atomically (`_write_marker`, a temp
file in the same directory then `os.replace`) and, if it does not parse, `adopt` says so and stops before `init`
runs rather than skip the problem.

Hooks — `hook <name>` is what `hooks/hooks.json` (plugin) and `settings.json` (clone) run, with Claude Code's hook
JSON on stdin. Every hook is a silent no-op (exit 0, no output) when ctx is not installed, when no store is named
or found, or when anything in the adapter itself fails, so a machine that has not adopted ctx-store sees nothing:

  pre-tool-use         a Write/Edit/MultiEdit/NotebookEdit of a `*.md` doc under an adopted content root → deny,
                       naming the ctx tool to use instead; exempt: the index's skip dirs (bin _templates sessions
                       handoff memory state), dot dirs, the generated catalogs and what the kit's store settings
                       ignore. Anything else, or any error: no decision
  post-tool-use        a Write/Edit under the content root → `ctx validate --changed --adopt`; a finding (exit 3)
                       comes back as `{"systemMessage": …}`; any exit but 0 or
                       NO_STORE (or a timeout) comes back as `ctx validate did not run: …`
  post-tool-use-async  the same trigger, or a write through a ctx MCP tool → `ctx touch --session <session_id>` (the
                       registry row `session register` stamped with the harness session id), then the kit's
                       catalogs INDEX.md (gen_index.py) and SESSION_INDEX.md (gen_sessions.py --no-archive) are
                       regenerated — with or without ctx; a failure goes to the scratch dir's hooks.log only
  brief-registry       SessionStart startup|resume|clear → `ctx brief --registry`, byte-budgeted
  brief-session        SessionStart compact → `ctx brief --session <session_id>`, byte-budgeted

The store a call names: `CTX_STORE` when set (ctx reads it itself), else `--store <content root>`
(kit_profile.context_root()) — a write always names its store. `adopt` and `pre-tool-use` always name the content root.

  python3 ctx_adapter.py version          # the pinned tag, then `api <CTX_API>` on a second line
  python3 ctx_adapter.py where            # the ctx executable; exit 1 when not installed
  python3 ctx_adapter.py install          # fetch the pinned tag into the pinned location (no-op when present)
  python3 ctx_adapter.py adopt [--check] [--replace]  # ctx init with the kit's settings; --check only reports
  python3 ctx_adapter.py mcp              # the ctx MCP server on the store; each write tool call names its own `actor`
                                           # (the caller's registered session name) — MCP_ACTOR (or CTX_ACTOR) is
                                           # only the floor for a write that names none
  python3 ctx_adapter.py mcp-json <file>  # add that server to a .mcp.json (clone installs; never replaces an entry)
  python3 ctx_adapter.py ctx <verb> …     # run one ctx verb on the store (the Bash route when the MCP tools are absent);
                                           # CTX_ACTOR defaults to the registered session name when one is on file
  python3 ctx_adapter.py hook <name>      # one of the hooks above; hook JSON on stdin

Exit codes: 0 ok · 1 not installed · 2 usage or I/O error (one stderr line) · 3 adopted, with validation findings ·
4 not adopted (`adopt --check`) · 5 adopted, but a store file differs from the kit's (kept; `adopt --replace`) — 3
takes precedence when both hold (the `differs:` lines still print). `ctx` and `mcp` exit as ctx does. A hook always exits 0. Stdlib only.
"""
from __future__ import annotations
import argparse
import contextlib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CTX_VERSION = "v0.6.0"  # the ctx-store release tag the kit's adapters are written against — bump here only
CTX_API = 1             # the ctx API `ctx --version` must report (its `(api N)` suffix) — bump only alongside a
                         # verb/output change the adapter now relies on; `ctx_adapter.py version`'s second line
                         # exposes it so kit-health can catch a pinned install answering a different one
CTX_REPO = "https://github.com/MdaaaaO/ctx-store"
BRIEF_BUDGET = 2048     # bytes of a SessionStart brief (ctx's default is 4096; the start of a session is prime context)
HOOK_TIMEOUT = 8        # seconds one ctx call may take inside a hook (the hook entries allow 10)
LOCK_TIMEOUT = "3"      # CTX_LOCK_TIMEOUT for a hook's ctx call unless the user set one: a held lock must not stall a tool
MESSAGE_MAX = 1000      # characters of validate findings returned as the systemMessage
CONTEXT_TOOLS = ("file_path", "notebook_path")  # the tool_input fields that name the file a Write/Edit/NotebookEdit changed
BIN = Path(__file__).resolve().parent
STORE_DATA = BIN.parent / "ctx-store"  # the kit's store settings + type schemas, handed to `ctx init` by `adopt`
ADOPT_TIMEOUT = 300     # seconds `adopt` gives one ctx call (a whole-store validate; not a hook)
INSTALL_TIMEOUT = 120   # seconds `install` gives the pinned-tag clone (setup.sh runs it unattended)
MCP_ACTOR = "claude"    # CTX_ACTOR of the MCP server unless the user set one: the audit rows of the model's writes
# A write through the ctx MCP server's tools (plugin: mcp__plugin_<plugin>_ctx__…, a clone's .mcp.json: mcp__ctx__…).
CTX_WRITE_TOOL = re.compile(r"^mcp__(?:\w[\w-]*_)?ctx__ctx_(?:create|str_replace|insert|delete|rename|log|fm|new|move|maintain|migrate)$")
DENY_HINT = ("write it through the ctx tools instead: `ctx_str_replace` (one exact string), `ctx_insert` (after a line), "
             "`ctx_log` (a Session-log line), `ctx_fm` (a frontmatter field), `ctx_create` (the whole text), `ctx_new` "
             "(a new doc) — from Bash: `python3 $BATON/context-db/bin/ctx_adapter.py ctx <verb> …` (`ctx help <verb>`)")


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
        # setup.sh runs this unattended: a stalled network or a credential prompt must fail, never hang
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        try:
            r = subprocess.run(["git", "-c", "advice.detachedHead=false", "clone", "-q", "--depth", "1", "--branch",
                                version, url, str(src)], capture_output=True, text=True, env=env,
                               stdin=subprocess.DEVNULL, timeout=INSTALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise OSError(f"git clone of {version} did not finish within {INSTALL_TIMEOUT}s (network stalled?)")
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


# ── adopt ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _code(r: subprocess.CompletedProcess) -> str:
    """The error code of a failed ctx call (`NO_STORE`, `NO_SUCH_DOC`, …), "" when there is none."""
    m = re.match(r"[A-Z_]+", r.stderr.strip())
    return m.group(0) if m and r.returncode != 0 else ""


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _settings_for_init() -> tuple[Path, dict | None]:
    """The file `ctx init --settings` gets: the kit's `ctx-store.json`, minus `mcp` when it has one. The pinned
    ctx's `init` bootstrap predates the `mcp.actors` setting (ctx-store#66/#71) and refuses any settings file
    that names it (`SCHEMA_VIOLATION mcp`), even though every other ctx entry point reads `mcp.actors` straight
    from `ctx-store.json` once it is there. `adopt` hands `init` everything `init` knows and applies `mcp`
    itself, below — until `init` catches up, the one exception to "the kit never writes a store file"."""
    data = json.loads((STORE_DATA / "ctx-store.json").read_text(encoding="utf-8"))
    mcp = data.pop("mcp", None)
    if mcp is None:
        return STORE_DATA / "ctx-store.json", None
    fd, name = tempfile.mkstemp(prefix="ctx-store-settings-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return Path(name), mcp


def _init_marker(data: dict) -> bytes:
    """The exact bytes `ctx init` itself writes for a settings marker (`ctxstore.bootstrap._marker`, pinned
    CTX_VERSION) — matched byte for byte, so a file this writes digests to what `init`'s own audit row records
    for it (ctx-store#73)."""
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _write_marker(path: Path, data: dict) -> None:
    """Write a store marker atomically: `init`'s own bytes (`_init_marker`) to a temp file in `path`'s own
    directory, then `os.replace` — so a write interrupted here leaves either the old marker or the new one, never
    a truncated file the next parse trips on."""
    fd, tmp = tempfile.mkstemp(prefix=".ctx-store-", suffix=".json.tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(_init_marker(data))
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_marker(root: Path) -> dict | None:
    """Parse `ctx-store.json`'s marker. None when the file does not exist yet — a fresh store, nothing for either
    `mcp` helper to do. Raises ValueError, message fit to print, when the file exists but its bytes do not parse
    as a JSON object: callers must not skip that silently (a truncated marker from an interrupted write is exactly
    what `_write_marker` above now prevents; one that still does not parse is treated as a real problem)."""
    marker = root / "ctx-store.json"
    try:
        text = marker.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        current = json.loads(text)
    except ValueError as e:
        raise ValueError(str(e)) from e
    if not isinstance(current, dict):
        raise ValueError("not a JSON object")
    return current


def _strip_mcp_for_upgrade(root: Path) -> dict | None:
    """Before `ctx init --upgrade` runs (never for a fresh init or `--replace`, where this cannot matter): strip
    `mcp` off the marker, if it parses and still carries one, so the file's bytes go back to exactly what the last
    `init` wrote. `init --upgrade` (ctxstore.bootstrap) decides `kept` vs `written`/`unchanged` by comparing the
    marker's digest to the hash its own audit row recorded, not by reading the file itself — stripped, that digest
    lines up again and a kit settings change reaches the store normally (ctx-store#73). A local `mcp` is not
    supported (the kit owns this key: its `actors` pattern is `session.py`'s own NAME_RE, and a local pattern would
    break session writes) — every other key follows `init`'s own kept rule, but whatever value this removes is
    replaced by `_apply_mcp_setting`, below, once `init` has run; the caller reports the swap, loudly, when the
    removed value was not already the kit's. Returns the removed value, or None when there was none to remove.
    Raises ValueError (see `_read_marker`) when the marker exists but does not parse — the caller stops before
    `init` runs rather than skip the problem."""
    current = _read_marker(root)
    if current is None or "mcp" not in current:
        return None
    removed = current.pop("mcp")
    _write_marker(root / "ctx-store.json", current)
    return removed


def _apply_mcp_setting(root: Path, mcp: dict) -> None:
    """`ctx-store.json`'s `mcp` key, since `init` cannot write it (`_settings_for_init` above) and a local value is
    not supported: put the kit's value back in, atomically (`_write_marker`) — after `init` has run, whether it
    succeeded or not, so a failed upgrade never leaves the store without its actors pattern. A marker that does not
    parse at this point was already reported by `_strip_mcp_for_upgrade` before `init` ran, or is a problem the
    next `adopt` will catch the same way — this does nothing further rather than mask `init`'s own exit code."""
    try:
        current = _read_marker(root)
    except ValueError:
        return
    if current is None:
        return
    if current.get("mcp") != mcp:
        current["mcp"] = mcp
        _write_marker(root / "ctx-store.json", current)


def adopt(check: bool = False, replace: bool = False) -> int:
    ctx, why = resolve()
    if ctx is None:
        print(why, file=sys.stderr)
        return 1
    root = _context_root()
    if not root.is_dir():
        print(f"ctx_adapter.py adopt: no content root at {root} — run setup.sh first", file=sys.stderr)
        return 2
    store = ["--store", str(root)]
    differs, blocked = False, []
    if check:
        r = _ctx(ctx, store, "validate", timeout=ADOPT_TIMEOUT)
        if _code(r) == "NO_STORE":
            print(f"not adopted: {root} is not a ctx store — run `python3 $BATON/context-db/bin/ctx_adapter.py adopt`")
            return 4
    else:
        settings_path, mcp = _settings_for_init()
        removed_mcp = None
        if not replace and mcp is not None:
            try:
                removed_mcp = _strip_mcp_for_upgrade(root)  # undo the last patch so --upgrade's digest check sees init's own bytes
            except ValueError as e:
                settings_path.unlink(missing_ok=True)  # the temp file _settings_for_init wrote; never the kit's own
                print(f"ctx_adapter.py adopt: ctx-store.json does not parse — {e}", file=sys.stderr)
                return 2
        try:
            r = _ctx(ctx, store, "init", "--settings", str(settings_path),
                     "--types", str(STORE_DATA / "types"), "--replace" if replace else "--upgrade", timeout=ADOPT_TIMEOUT)
        finally:
            if mcp is not None:
                settings_path.unlink(missing_ok=True)  # the temp file _settings_for_init wrote; never the kit's own
                _apply_mcp_setting(root, mcp)  # ctx-store#73: put it back whatever init just did to the file
        if r.returncode != 0:
            print(f"ctx_adapter.py adopt: ctx init failed: {(_lines(r.stderr) or [f'exit {r.returncode}'])[0]}",
                  file=sys.stderr)
            return 2
        out = _lines(r.stdout)
        print((out or [f"ok: store {root}"])[0])
        kept = [ln.split(":", 1)[1].strip() for ln in out if ln.startswith("kept:")]  # edited here: it stays
        mcp_replaced = removed_mcp is not None and removed_mcp != mcp  # a local mcp is not supported: always ours
        if mcp_replaced:
            print("replaced: ctx-store.json mcp — the kit owns this key (session.py's name pattern); "
                  "a local value is not kept")
        differs = bool(kept) or mcp_replaced
        for f in kept:
            print(f"differs: {f} — kept (edited here); `ctx_adapter.py adopt --replace` takes the kit's")
        # a type the kit moved to a new schema version leaves its docs MIGRATION_PENDING, and ctx refuses writes to
        # those: bring them along now (ctx's migrations are idempotent; a doc it cannot migrate is a finding below)
        m = _ctx(ctx, store, "migrate", "--apply", timeout=ADOPT_TIMEOUT)
        for ln in _lines(m.stdout):
            print(f"migrated: {ln}")
        blocked = _lines(m.stderr) if m.returncode == 3 else []  # a doc invalid on its own cannot take the step
        if m.returncode not in (0, 3):
            print(f"ctx_adapter.py adopt: ctx migrate failed: {(_lines(m.stderr) or [f'exit {m.returncode}'])[0]}",
                  file=sys.stderr)
            return 2
        r = _ctx(ctx, store, "validate", timeout=ADOPT_TIMEOUT)
    if r.returncode not in (0, 3):
        print(f"ctx_adapter.py adopt: ctx validate failed: {(_lines(r.stderr) or [f'exit {r.returncode}'])[0]}",
              file=sys.stderr)
        return 2
    findings = _lines(r.stderr) if r.returncode == 3 else []
    print(f"store {root}: " + (_lines(r.stdout) or ["ok"])[0])
    for f in findings:
        print(f"finding: {f}")
    for f in [] if check else blocked:
        print(f"finding: migrate: {f} (fix the doc, then re-run adopt)")
    findings = findings + ([] if check else blocked)
    if not check:
        a = _ctx(ctx, store, "validate", "--changed", "--adopt", timeout=ADOPT_TIMEOUT)
        print("adopt: " + ((_lines(a.stdout) or ["ok"])[0] if a.returncode in (0, 3) else
                           (_lines(a.stderr) or [f"exit {a.returncode}"])[0]))
        if a.returncode not in (0, 3):
            return 2
    return 3 if findings else 5 if differs else 0


# ── the MCP server and the Bash route ──────────────────────────────────────────────────────────────────────────
def _null_mcp(why: str) -> int:
    """An MCP server with no tools, for a project without a ctx or a content root: the plugin's server is started in
    every project, and a failed server there would be noise. It answers initialize, tools/list and ping."""
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict) or "id" not in m:
            continue  # a notification
        method = m.get("method")
        if method == "initialize":
            params = m.get("params") if isinstance(m.get("params"), dict) else {}
            res: dict = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                         "serverInfo": {"name": "ctx", "version": "none"}, "instructions": why}
            out = {"jsonrpc": "2.0", "id": m["id"], "result": res}
        elif method in ("tools/list", "ping"):
            out = {"jsonrpc": "2.0", "id": m["id"], "result": {"tools": []} if method == "tools/list" else {}}
        else:
            out = {"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": f"{why}"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
    return 0


def run_ctx(args: list[str], mcp: bool = False) -> int:
    """Exec ctx on the store (`CTX_STORE`, else `--store <content root>`). The MCP server's own CTX_ACTOR default
    is MCP_ACTOR — a per-tool-call `actor` (the store's `mcp.actors` pattern) is what the owner rule and the audit
    row actually see, so this is only the floor for a write that names none. The Bash fallback
    (`ctx_adapter.py ctx <verb> …`) has no per-call actor, so it defaults CTX_ACTOR to this session's registered
    name (`kit_profile.py session-name`) when one is on file — an unregistered session still falls through to
    ctx's own default ($USER/$LOGNAME)."""
    ctx, why = resolve()
    root = _context_root()
    store = _store_args(root)
    if ctx is None or store is None:
        why = why or f"no content root at {root}"
        if mcp:
            return _null_mcp(f"ctx-store is not available here: {why}")
        print(f"ctx_adapter.py ctx: {why}", file=sys.stderr)
        return 1
    env = dict(os.environ)
    if mcp:
        env.setdefault("CTX_ACTOR", MCP_ACTOR)
    elif "CTX_ACTOR" not in env:
        sys.path.insert(0, str(BIN))
        import kit_profile  # same dir
        name = kit_profile.session_name()
        if name:
            env["CTX_ACTOR"] = name
    sys.stdout.flush()
    os.execve(str(ctx), [str(ctx), *store, *args], env)
    return 0  # not reached


def mcp_json(path: Path) -> int:
    """Add the ctx server to a project `.mcp.json` (a clone's workspace root; a plugin install ships it in plugin.json).
    An existing `ctx` entry is the user's and is kept."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as e:
        print(f"ctx_adapter.py mcp-json: cannot read {path}: {e}", file=sys.stderr)
        return 2
    servers = data.setdefault("mcpServers", {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        print(f"ctx_adapter.py mcp-json: {path} has no mcpServers object", file=sys.stderr)
        return 2
    if "ctx" in servers:
        print(f"{path}: the ctx server is already there")
        return 0
    servers["ctx"] = {"command": "python3", "args": [str(Path(__file__).resolve()), "mcp"]}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    print(f"{path}: added the ctx server")
    return 0


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
    sys.path.insert(0, str(BIN))
    import kit_profile  # same dir
    return kit_profile.context_root()


def _store_args(root: Path) -> list[str] | None:
    """The store a hook's call names: nothing when CTX_STORE is set (ctx reads it), else `--store <content root>`;
    None when there is neither (no content root on disk) — the hook then does nothing."""
    if os.environ.get("CTX_STORE", "").strip():
        return []
    return ["--store", str(root)] if root.is_dir() else None


def _ctx(ctx: Path, store: list[str], *args: str, timeout: int = HOOK_TIMEOUT) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("CTX_LOCK_TIMEOUT", LOCK_TIMEOUT)
    return subprocess.run([str(ctx), *store, *args], env=env, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def _targets(payload: dict, root: Path) -> list[str]:
    """The files the tool call names that lie under the content root, as paths relative to it (symlinks resolved:
    `memory/`, the harness auto-memory symlink, resolves outside the root)."""
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return []
    base = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    real_root = os.path.realpath(root)
    out = []
    for field in CONTEXT_TOOLS:
        f = ti.get(field)
        if isinstance(f, str) and f:
            p = os.path.realpath(os.path.join(base, os.path.expanduser(f)))
            if os.path.commonpath([p, real_root]) == real_root:
                out.append(os.path.relpath(p, real_root).replace(os.sep, "/"))
    return out


def _changed_under(payload: dict, root: Path) -> bool:
    """True when the tool call changed a file under the content root."""
    return bool(_targets(payload, root))


def _exempt(rel: str) -> bool:
    """A path under the content root a model tool may write: not a `*.md` doc, a dot dir, one of the index's skip
    dirs, a generated catalog, or a pattern the kit's store settings mark `generated` or `ignore` (no ctx verb
    writes those)."""
    import gen_index  # same dir (on sys.path via _context_root): the index's skip dirs are the one list
    parts = rel.split("/")
    if not rel.endswith(".md") or rel == "." or any(x.startswith(".") for x in parts):
        return True
    if set(parts[:-1]) & gen_index.SKIP_DIRS or rel in ("INDEX.md", "SESSION_INDEX.md") or parts[-1] in gen_index.SKIP_FILES:
        return True
    settings = json.loads((STORE_DATA / "ctx-store.json").read_text(encoding="utf-8"))
    return any(fnmatch.fnmatch(rel, g) for g in [*settings.get("generated", []), *settings.get("ignore", [])])


def _deny(payload: dict, ctx: Path, root: Path) -> str:
    docs = [t for t in _targets(payload, root) if not _exempt(t)]
    if not docs:
        return ""
    key = docs[0][:-3]
    r = _ctx(ctx, ["--store", str(root)], "get", key)
    if r.returncode != 0 and _code(r) != "NO_SUCH_DOC":
        return ""  # NO_STORE (not adopted) or any other answer: no decision — fail open
    tool = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else "this tool"
    reason = (f"`{docs[0]}` is a doc in the adopted ctx store (key `{key}`), so {tool} may not write it — "
              f"{DENY_HINT}. Direct writes stay open under sessions/, state/, memory/ and the other index skip dirs.")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})


def _log(line: str) -> None:
    """One line to the scratch dir's hooks.log (the SessionStart hook's log); never raises."""
    try:
        import kit_profile
        log = kit_profile.scratch() / "hooks.log"
        if not log.is_symlink():
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"ctx_adapter.py: {line}\n")
    except Exception:  # noqa: BLE001 — the log is best effort
        pass


def refresh_catalogs(root: Path) -> None:
    """Regenerate the kit's catalogs (INDEX.md, SESSION_INDEX.md) after a change under the content root. The sweep of
    ended sessions into the archive is left to `session-*`; a failure is logged, never shown."""
    env = dict(os.environ, CONTEXT_ROOT=str(root))
    for args in (["gen_index.py"], ["gen_sessions.py", "--no-archive"]):
        try:
            r = subprocess.run([sys.executable, str(BIN / args[0]), *args[1:]], env=env, cwd=BIN, capture_output=True,
                               text=True, timeout=HOOK_TIMEOUT, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                _log(f"{args[0]} exit {r.returncode}: {(_lines(r.stderr) or [''])[-1][:300]}")
        except (OSError, subprocess.SubprocessError) as e:
            _log(f"{args[0]}: {e}")


def _session_id(payload: dict) -> str:
    sid = payload.get("session_id")
    return sid.strip() if isinstance(sid, str) else ""


def hook(name: str) -> str:
    """What the hook prints (maybe nothing). Raises nothing the caller must handle beyond Exception."""
    payload = _payload()
    ctx, _ = resolve()
    if name == "post-tool-use-async":
        root = _context_root()
        via_ctx = bool(CTX_WRITE_TOOL.match(str(payload.get("tool_name", ""))))
        if not root.is_dir() or not (via_ctx or _changed_under(payload, root)):
            return ""
        store = _store_args(root)
        sid = _session_id(payload)
        if ctx is not None and store is not None and sid:
            try:
                _ctx(ctx, store, "touch", "--session", sid)
            except (OSError, subprocess.SubprocessError) as e:
                _log(f"ctx touch: {e}")
        refresh_catalogs(root)
        return ""
    if ctx is None:
        return ""
    root = _context_root()
    if name == "pre-tool-use":
        return _deny(payload, ctx, root) if root.is_dir() else ""
    store = _store_args(root)
    if store is None:
        return ""
    if name == "post-tool-use":
        if not _changed_under(payload, root):
            return ""  # a write through a ctx tool was validated by ctx itself
        try:
            r = _ctx(ctx, store, "validate", "--changed", "--adopt")
        except subprocess.TimeoutExpired:
            return json.dumps({"systemMessage": f"ctx validate did not run: no answer within {HOOK_TIMEOUT}s"})
        lines = _lines(r.stderr)
        if r.returncode == 3:  # validation findings
            return json.dumps({"systemMessage": f"ctx validate: {' · '.join(lines)[:MESSAGE_MAX]}"})
        if r.returncode == 0 or _code(r) == "NO_STORE":
            return ""  # clean, or the content root is not a store yet (not adopted): nothing to say
        # Any other exit (usage, lock timeout, read-only, a ctx that changed its verbs) means validation did
        # not happen; staying silent would read exactly like "no findings".
        first = lines[0] if lines else f"exit {r.returncode}"
        return json.dumps({"systemMessage": f"ctx validate did not run: {first[:MESSAGE_MAX]}"})
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


HOOKS = ("pre-tool-use", "post-tool-use", "post-tool-use-async", "brief-registry", "brief-session")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["ctx"]:  # everything after `ctx` is ctx's own command line, options included
        return run_ctx(argv[1:])
    p = argparse.ArgumentParser(prog="ctx_adapter.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version", help="print the pinned ctx-store tag")
    sub.add_parser("where", help="print the ctx executable; exit 1 when not installed")
    sub.add_parser("install", help="fetch the pinned tag into the pinned location")
    ap = sub.add_parser("adopt", help="make the content root a ctx store with the kit's settings (ctx init)")
    ap.add_argument("--check", action="store_true", help="report only: exit 4 when not adopted, 3 on findings")
    ap.add_argument("--replace", action="store_true", help="overwrite store files that differ from the kit's")
    sub.add_parser("mcp", help="run the ctx MCP server on the store (stdio)")
    mp = sub.add_parser("mcp-json", help="add the ctx MCP server to a project .mcp.json")
    mp.add_argument("file", type=Path)
    sub.add_parser("ctx", help="run one ctx verb on the store: ctx_adapter.py ctx <verb> [arguments]")
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
        print(f"api {CTX_API}")
        return 0
    if a.cmd == "where":
        ctx, why = resolve()
        if ctx is None:
            print(why, file=sys.stderr)
            return 1
        print(ctx)
        return 0
    if a.cmd == "mcp":
        return run_ctx(["mcp"], mcp=True)
    if a.cmd == "mcp-json":
        return mcp_json(a.file)
    try:
        if a.cmd == "adopt":
            return adopt(a.check, a.replace)
        print(install())
    except (OSError, subprocess.SubprocessError) as e:
        print(f"ctx_adapter.py {a.cmd}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
