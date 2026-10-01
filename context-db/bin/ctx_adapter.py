#!/usr/bin/env python3
"""ctx_adapter.py — the kit's one adapter to ctx-store, the context-store CLI `ctx`.

The kit talks to ctx through its verbs only: it never reads or writes the store's own files (`ctx-store.json`,
`.ctx/`, `.audit/`), and `CTX_STORE` is an opaque locator it passes through, never a path it inspects. Whether a
store exists is ctx's answer (`NO_STORE`), not a file test here. The one exception is `adopt` (below).

Pin — `CTX_VERSION` below is the one place the kit names the ctx-store release it is written against. It is
fetched, not vendored: `install` clones exactly that tag (`git clone --depth 1 --branch <tag>`) into a per-user
cache directory whose path carries the tag, so a bumped pin never picks up an older copy. `CTX_SHA` beside it
names the commit that tag resolves to right now: `install` reads the clone's detached HEAD and refuses to apply
a clone whose commit disagrees (naming both shas) — a tag repointed at another commit after the kit was pinned
to it is exactly what this catches, not a network failure. When the clone's own commit cannot be read at all
(no git on PATH mid-clone, a corrupted checkout), the fetch is applied anyway and recorded unverified rather than
blocked — a verify that cannot run is not the same as one that ran and disagreed. `install` writes what it found
next to the pinned `ctx` (`pin status`, below); `kit-health`'s ctx pin line reads it back, offline, so a moved tag
or a cache installed before a pin bump is reported without a second clone.

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
`adopt` always replaces it with the kit's value, never silently: `updated: ctx-store.json mcp — the kit owns this
key (session.py's name pattern); the previous value was replaced` prints (exit code unaffected: a kit release that
changes its own pattern looks the same as a local edit, and neither is a `kept` file) whenever the value replaced
was not already the kit's; an equal value prints nothing. `--upgrade` decides
`kept` vs `written` by the marker's digest, not its content (ctx-store#73), so before it runs the adapter strips its
own last `mcp` patch off the marker (the digest then matches what `init` last wrote) and puts the kit's `mcp` value
back once `init` is done, reporting the swap as above. The marker is written atomically (`_write_marker`, a temp
file in the same directory then `os.replace`) and, if it does not parse, `adopt` says so and stops before `init`
runs rather than skip the problem.

Behind — a full `adopt` also records, under the store's own ignored `state/` dir (`state/ctx-adapter/adopted-kit.json`
— `state/**` is in `ctx-store.json`'s own `ignore` list, so this is never a file `ctx` itself tracks or writes
through a verb): a digest of the kit's store settings and type schemas (`ctx-store.json` plus every `types/*.json`,
the same files `--settings`/`--types` hand `ctx init`), this kit's own release version (`.claude-plugin/plugin.json`'s
`version` — the field `kit_profile.plugin_install`/`kit-health.py`'s `kit_version` already read, reused here rather
than a second source), and, when `ctx init --upgrade` kept a locally edited store file (`differs`, exit 5, above),
the list of what it kept. `adopt --check` recomputes the digest and compares it with what is on record: a mismatch
is `behind`, exit 6 (`python3 $BATON/context-db/bin/ctx_adapter.py adopt` catches up) — unless the record names a
kit version strictly newer than this kit's own, in which case the store was adopted by a newer kit and this one is
the one out of date: one line `ahead: …` and exit 0, never `adopt`'s own advice, which would hand the store an
older kit's types. No recorded version, or one that does not parse, compares as unknown and falls back to `behind`
like any other mismatch. A digest match with a recorded `kept` list still reports it — one `differs:` line per
file, exit 5 — a kept file does not stop being a local edit just because nothing else about the store moved. No
record at all (a store adopted before any of this existed) is the least surprising case to treat as `behind`: one
plain `adopt` run (no `--check`) brings the record current, kept list, version and all. `adopt --check --no-validate`
answers the same `behind`/`ahead`/`differs` lines from the record alone, skipping `ctx validate` entirely — it needs
no ctx executable at all, so it can never take long or time out; "is this an adopted store" is then a file test
(the store's own `ctx-store.json` at the content root) rather than ctx's own answer, the one exception named above.
The same version record
also guards a plain `adopt`: one whose own kit is older than what the store last recorded refuses outright (exit
2, naming both versions, `--replace` overrides) rather than hand the store an older release's types — ctx-store's
own `init --upgrade` does not check a type's `version` against what is already installed (it diffs bytes and the
last-init digest only, `ctxstore/bootstrap.py`'s `init`), so nothing else would catch a silent downgrade.

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
  brief-session        SessionStart compact → one owner line ("compacted — re-grounded from sessions/<name> and epic
                       <epic key>", the session row's own `epic:` frontmatter value verbatim, or "and no
                       context doc" when the row carries no `epic:` field at all), then `ctx brief --session
                       <session_id>` re-budgeted by this adapter — its frontmatter cut to
                       `session`/`epic`/`working_on`/`responsibilities` (`stats`, `heartbeat`, `session_id`,
                       `ref`, `updated` and the `sections` summary dropped outright, the fields a 2026-10-01
                       gap report found eating the budget before the body) and its `##` sections led by
                       `SECTION_PRIORITY` when present — within `BRIEF_BUDGET`. Both are written and flushed to
                       stdout before any context-doc lookup is even attempted: only once they are on stdout
                       does this try to resolve the context doc the `epic:` field names through the store's
                       own `resolve` rule, then — when one resolved — fetch its key and the head of its
                       *Remaining work*, within a separate `EPIC_BUDGET`. The whole hook has `COMPACT_DEADLINE`
                       seconds (under the hooks' own 10s `timeout`); the context-doc lookups (`resolve`/
                       `get`) run only while more than 1.5s of it remain before each one starts, every
                       one of them capped at `min(EPIC_LOOKUP_TIMEOUT, remaining)` recomputed right before
                       that call, never a single value reused across more than one — skipped or timed out, one
                       line `context doc: skipped (hook deadline)` stands in for that tail

The store a call names: `CTX_STORE` when set (ctx reads it itself), else `--store <content root>`
(kit_profile.context_root()) — a write always names its store. `adopt` and `pre-tool-use` always name the content root.

  python3 ctx_adapter.py version          # the pinned tag, `api <CTX_API>` on a second line, `sha <CTX_SHA>` on a third
  python3 ctx_adapter.py where            # the ctx executable; exit 1 when not installed
  python3 ctx_adapter.py install          # fetch the pinned tag into the pinned location (no-op when present)
  python3 ctx_adapter.py pin              # the sha `install` found at the pinned location, and whether it verified;
                                           # exit 1 when nothing is installed or the copy predates this check
  python3 ctx_adapter.py adopt [--check [--no-validate]] [--replace]  # ctx init with the kit's settings; --check
                                           # only reports; --check --no-validate skips the whole-store `ctx
                                           # validate` and only compares the recorded digest — cheap, never touches ctx
  python3 ctx_adapter.py mcp              # the ctx MCP server on the store; each write tool call names its own `actor`
                                           # (the caller's registered session name) — MCP_ACTOR (or CTX_ACTOR) is
                                           # only the floor for a write that names none
  python3 ctx_adapter.py mcp-json <file>  # add that server to a .mcp.json (clone installs; never replaces an entry)
  python3 ctx_adapter.py ctx <verb> …     # run one ctx verb on the store (the Bash route when the MCP tools are absent);
                                           # CTX_ACTOR defaults to the registered session name when one is on file
  python3 ctx_adapter.py hook <name>      # one of the hooks above; hook JSON on stdin

Exit codes: 0 ok (one line `ahead: …` when `--check` finds the store last adopted by a newer kit than this one) ·
1 not installed · 2 usage or I/O error, one stderr line (a plain `adopt` also refuses this way, leaving the store
untouched, when the record names a kit version newer than this one's — `--replace` overrides it; `--no-validate`
without `--check` is the same usage error) · 3 adopted, with validation findings — a store that is also behind
still prints its own `behind:` line alongside the findings, same exit code · 4 not adopted (`adopt --check`;
`--no-validate` answers this from a `ctx-store.json` file test at the content root rather than asking ctx) · 5
adopted, but a store file differs from the kit's — kept, one `differs:` line per file (`adopt --replace` takes the
kit's); `--check` reports the same files from its own record rather than re-running `ctx init` · 6 adopted, but the
store's recorded digest of the kit's settings/types is missing or stale, and no newer-kit record explains the gap
(`adopt --check` only — a plain `adopt` records a fresh one; `--no-validate` skips `ctx validate` entirely and only
compares the record, so findings cannot occur there — 0/5/6 still answer, and it never touches ctx). Precedence
when more than one would apply: 3 over 6 over 5 over 0. `ctx` and `mcp` exit as ctx does.
A hook always exits 0. Stdlib only.
"""
from __future__ import annotations
import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

CTX_VERSION = "v0.6.0"  # the ctx-store release tag the kit's adapters are written against — bump here only
CTX_SHA = "d706db379d4ae5814c01a6a31837f4eebff319c5"  # the commit CTX_VERSION's tag names right now — bump together
                         # with CTX_VERSION, from the tag's own commit (`git rev-parse <tag>^{commit}`), never
                         # from the tag object's own sha (an annotated tag's `git rev-parse <tag>` alone)
CTX_API = 1             # the ctx API `ctx --version` must report (its `(api N)` suffix) — bump only alongside a
                         # verb/output change the adapter now relies on; `ctx_adapter.py version`'s second line
                         # exposes it so kit-health can catch a pinned install answering a different one
CTX_REPO = "https://github.com/MdaaaaO/ctx-store"
BRIEF_BUDGET = 2048     # bytes of a SessionStart brief (ctx's default is 4096; the start of a session is prime context)
EPIC_BUDGET = 600       # bytes of a compact brief's context-doc tail (its key plus the head of its Remaining work)
REMAINING_WORK_HEAD = 8  # lines of a context doc's Remaining work section a compact brief carries
SESSION_RAW_BUDGET = 65536  # bytes `ctx brief --session --full` may answer with before this adapter filters and
                            # re-budgets it itself — large enough that no ordinary session doc is cut before that
EPIC_LOOKUP_TIMEOUT = 3  # seconds either extra ctx call (resolve, get) a compact brief makes may take: the session
                         # brief itself already has HOOK_TIMEOUT; these two are nice-to-have, not worth the same wait
SESSION_BRIEF_KEEP = ("session", "epic", "working_on", "responsibilities")  # the rest of a session doc's
    # frontmatter (stats, heartbeat, session_id, ref, updated) is what a 2026-10-01 gap report measured eating a
    # compact brief's budget before the body ever got any; this adapter keeps only these four fields, in this order
RESPONSIBILITIES_MAX = 160  # characters of a kept `responsibilities` field a compact brief carries
SECTION_PRIORITY = ("Open PRs", "Open decisions", "Assumptions", "Owns", "Worktrees")  # a compact brief's body
                    # leads with these `##` sections, in this order, when present; every other section (there may
                    # be none) keeps its original relative order after them
SESSION_FM_LINE_KEYS = {"session", "session_id", "ref", "status", "epic", "repos", "working_on",
                        "responsibilities", "stats", "heartbeat", "updated", "sections"}  # every line `ctx brief`
    # prints between a session doc's header and its body: its frontmatter fields plus the derived `sections: …`
    # summary line, both in the same "key: value" shape — the set this adapter's parser recognises and stops at
HOOK_TIMEOUT = 8        # seconds one ctx call may take inside a hook (the hook entries allow 10)
COMPACT_DEADLINE = 8    # seconds the whole brief-session hook may spend, start to finish (also under the hooks'
                        # own 10s "timeout" in hooks/hooks.json and settings.json, with a margin a slow
                        # process-start or lock wait can eat into): a compact brief used to make up to four ctx
                        # calls in a row with no shared budget, so a harness kill could lose even the owner
                        # line and session brief it had already finished computing — this bounds the whole
                        # hook and the owner line plus session brief are flushed to stdout as soon as they are
                        # ready, so only a skipped context-doc lookup is ever at risk
LOCK_TIMEOUT = "3"      # CTX_LOCK_TIMEOUT for a hook's ctx call unless the user set one: a held lock must not stall a tool
MESSAGE_MAX = 1000      # characters of validate findings returned as the systemMessage
CONTEXT_TOOLS = ("file_path", "notebook_path")  # the tool_input fields that name the file a Write/Edit/NotebookEdit changed
BIN = Path(__file__).resolve().parent
KIT_ROOT = BIN.parent.parent  # context-db/bin -> context-db -> the kit root, where .claude-plugin/plugin.json lives
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


PIN_STATUS_NAME = ".pin-status.json"  # beside the pinned `ctx`: what `install` found when it fetched this copy


def _clone_commit_sha(src: Path) -> str | None:
    """The commit the clone's detached HEAD sits at (`git rev-parse HEAD`) — read while `src/.git` still exists,
    before `install` strips it. None when that cannot be read (no git on PATH mid-clone, a corrupted checkout):
    the caller applies the fetch anyway and records it unverified rather than block on a check that could not
    run (owner decision, 2026-10-01: a verify that cannot run applies, unverified; only one that runs and
    disagrees refuses)."""
    try:
        r = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _write_pin_status(dest: Path, pinned_sha: str, cloned_sha: str | None, verified: bool) -> None:
    """Record, next to the pinned `ctx`, the sha `install` pinned against and what the clone's own commit turned
    out to be — atomically (temp file, then `os.replace`), so `pin_status` never reads a half-written file."""
    data = {"pinned_sha": pinned_sha, "cloned_sha": cloned_sha, "verified": verified}
    fd, tmp = tempfile.mkstemp(prefix=".pin-status-", suffix=".json.tmp", dir=str(dest))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, dest / PIN_STATUS_NAME)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def pin_status(dest: Path | None = None) -> dict | None:
    """The sha-verification record `install` wrote for the copy at `dest` (default: the pinned location for
    `CTX_VERSION`) — None when there is no copy there, or it predates this check (nothing to read)."""
    dest = dest or pinned_dir()
    try:
        return json.loads((dest / PIN_STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def install(url: str = CTX_REPO, version: str = CTX_VERSION, sha: str = CTX_SHA, dest: Path | None = None) -> Path:
    """Clone `version` of `url` into `dest` (default: the pinned location) and check it reports that version. When
    `sha` is set, the clone's own commit (`_clone_commit_sha`) must match it — a mismatch means the tag now points
    somewhere else and installs nothing, naming both shas; a commit that could not be read at all is not a
    mismatch, it is applied and recorded unverified (`_write_pin_status`; see `_clone_commit_sha`). The clone lands
    in a temporary sibling first and is renamed into place, so an interrupted fetch never leaves a half copy the
    resolver would take. Already present: returns it untouched (no re-check, no new record; a stale cache is
    kit-health's `pin` line to catch, see `ctx_pin_check` in kit-health.py — its fix removes the copy first)."""
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
        cloned_sha = _clone_commit_sha(src)  # must run before .git is stripped, below
        verified = False
        if sha:
            if cloned_sha and cloned_sha != sha:
                raise OSError(f"{version} now resolves to {cloned_sha}, not the pinned {sha} — "
                              "the tag may have moved; refusing to install")
            verified = cloned_sha == sha
        shutil.rmtree(src / ".git", ignore_errors=True)  # a pinned copy, not a checkout anyone should commit in
        try:
            os.replace(src, dest)
        except OSError:
            if not _usable(dest / "ctx"):  # a concurrent install won the rename: theirs is as good as ours
                raise
            return dest  # theirs stands, pin status included: writing ours over it would race it needlessly
        _write_pin_status(dest, pinned_sha=sha, cloned_sha=cloned_sha, verified=verified)
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


ADOPT_DIGEST_REL = Path("state") / "ctx-adapter" / "adopted-kit.json"  # under the store's own ignored `state/**`
    # (ctx-store.json's `ignore` list) — a record this adapter owns, never a file `ctx` tracks or writes through a verb


def _kit_data_digest() -> str:
    """sha256 over the kit's store settings (`ctx-store.json`) and every type schema under `STORE_DATA/types`,
    one relative name then its bytes, types sorted by filename — the same files `adopt` hands `ctx init`
    (`--settings`/`--types`, `_settings_for_init`), so a kit release that edits either changes this digest.
    `adopt --check` compares it with what the last `adopt` on this store recorded (`_recorded_state`) to
    report the store `behind`, without re-running `ctx init` or touching a store file itself."""
    h = hashlib.sha256()
    h.update(b"ctx-store.json\0")
    h.update((STORE_DATA / "ctx-store.json").read_bytes())
    for p in sorted((STORE_DATA / "types").glob("*.json")):
        h.update(f"types/{p.name}\0".encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _parse_semver(text: str) -> tuple[int, ...] | None:
    """`'0.7.0'` / `'v0.7.0'` -> `(0, 7, 0)`; None for anything else (blank, a pre-release, a `+N` suffix) — a
    version that cannot be parsed compares as unknown, never numerically, both for this kit's own version and a
    recorded one (`adopt`'s version-skew check, below, falls back to `behind` rather than guess an ordering)."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _kit_version() -> str:
    """This kit's own release version, `.claude-plugin/plugin.json`'s `version` field verbatim (e.g. `"0.7.0"`) —
    the same file `kit_profile.plugin_install`/`kit-health.py`'s `kit_version()` read for the same purpose,
    reused here rather than a second source; present on every install mode (plugin, clone, dev checkout), since
    the manifest ships in the kit itself. "" when the file is missing or does not parse — `_parse_semver("")` is
    None, so that compares as unknown, same as a store's own missing or unparseable record."""
    try:
        data = json.loads((KIT_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("version") or "")


def _adopt_state_path(root: Path) -> Path:
    return root / ADOPT_DIGEST_REL


def _write_adopt_digest(root: Path, kept: list[str] | None = None) -> None:
    """Record `_kit_data_digest()`, this kit's own version (`_kit_version`) and, when `ctx init --upgrade` kept a
    locally edited store file this run (`kept`, the same list `adopt` already prints `differs:` lines for), that
    list too — under the store's ignored `state/` dir, atomically (temp file in the same directory, then
    `os.replace` — the same pattern as `_write_marker`). Called once a full `adopt` has actually run `ctx
    init`/`migrate --apply` (never on an early-return error), so the record always reflects settings and types
    `ctx` was just handed, whatever `ctx validate` finds in the docs afterwards — `kept` is recorded right
    alongside the digest, not hidden behind it, so a `--check` that finds the digest clean still sees a kept
    file: a kept file must not read as clean just because nothing else about the store moved since."""
    path = _adopt_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {"digest": _kit_data_digest(), "version": _kit_version()}
    if kept:
        data["kept"] = list(kept)
    fd, tmp = tempfile.mkstemp(prefix=".adopted-kit-", suffix=".json.tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _recorded_state(root: Path) -> dict | None:
    """The `{digest, version, kept}` the last `adopt` on this store recorded (`kept` only when it kept a file;
    older records carry no `version`), or None — no record at all (a store adopted before this existed, or whose
    file does not parse). Either way `adopt --check` reports the store `behind` unless the record names a newer
    kit (see `adopt`): a missing record is the least surprising case to treat as behind, since one plain `adopt`
    run is all it takes to write a fresh one, and staying silent would mean a store that drifted from the kit
    right after this feature shipped goes unreported until something else happens to touch it."""
    try:
        data = json.loads(_adopt_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _check_record(root: Path) -> tuple[bool, bool]:
    """Compare the store's recorded adopted-kit state (`_recorded_state`) with this kit's own
    (`_kit_data_digest`/`_kit_version`), printing the same `behind:`/`ahead:`/`differs:` line(s) either
    `adopt --check` or its cheap `--no-validate` sibling answers with, and returning `(behind, differs)` for the
    caller's exit code. One implementation so the two readers can never disagree on wording."""
    rec = _recorded_state(root)
    if (rec or {}).get("digest") != _kit_data_digest():
        # the digest alone cannot tell "the kit moved on" from "a newer kit already adopted this store" — only
        # the recorded version can: strictly newer than this kit's own means this kit is the one behind, not the
        # store, so say that instead of pointing `adopt` at it (that would downgrade it)
        rec_version = str((rec or {}).get("version") or "")
        rec_v, own_v = _parse_semver(rec_version), _parse_semver(_kit_version())
        if rec_v is not None and own_v is not None and rec_v > own_v:
            print(f"ahead: the store was adopted by kit {rec_version} — this kit is {_kit_version() or 'unknown'}; "
                  "update the kit")
            return False, False
        print("behind: the store's settings or types predate the kit's — "
              "`python3 $BATON/context-db/bin/ctx_adapter.py adopt`")
        return True, False
    # the digest matches, but a store file `ctx init --upgrade` kept (edited here) at the last full `adopt`
    # still differs from the kit's own copy — a kept file does not stop being a local edit just because nothing
    # else about the store moved since
    kept = (rec or {}).get("kept") or []
    for f in kept:
        print(f"differs: {f} — kept (edited here); `ctx_adapter.py adopt --replace` takes the kit's")
    return False, bool(kept)


def adopt(check: bool = False, replace: bool = False, no_validate: bool = False) -> int:
    root = _context_root()
    if not root.is_dir():
        print(f"ctx_adapter.py adopt: no content root at {root} — run setup.sh first", file=sys.stderr)
        return 2
    if no_validate:
        # the cheap mode (--check only, enforced by the CLI): a record comparison alone, never ctx — so it can
        # never take long or time out. "Is this an adopted store" is then a file test (the store's own
        # `ctx-store.json` at the content root), the one exception to the module header's own rule, instead of
        # asking ctx for `NO_STORE`
        if not (root / "ctx-store.json").is_file():
            print(f"not adopted: {root} is not a ctx store — run `python3 $BATON/context-db/bin/ctx_adapter.py adopt`")
            return 4
        behind, differs = _check_record(root)
        return 6 if behind else 5 if differs else 0
    ctx, why = resolve()
    if ctx is None:
        print(why, file=sys.stderr)
        return 1
    store = ["--store", str(root)]
    differs, blocked = False, []
    if not check and not replace:
        # a kit older than the one the store last recorded would hand it older types — ctx-store's own
        # `init --upgrade` does not guard against that (it diffs bytes and the last-init digest only, never a
        # type's own `version`; see `ctxstore/bootstrap.py`'s `init`), so this adapter refuses before `init`
        # ever runs rather than silently downgrade the store
        rec_version = str((_recorded_state(root) or {}).get("version") or "")
        own_version = _kit_version()
        rec_v, own_v = _parse_semver(rec_version), _parse_semver(own_version)
        if rec_v is not None and own_v is not None and rec_v > own_v:
            print(f"ctx_adapter.py adopt: the store was last adopted by kit {rec_version}, newer than this kit "
                  f"({own_version or 'unknown'}) — `adopt --replace` to take the kit's types anyway", file=sys.stderr)
            return 2
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
        if removed_mcp is not None and removed_mcp != mcp:  # a local mcp is not supported: always the kit's; say so,
            print("updated: ctx-store.json mcp — the kit owns this key (session.py's name pattern); "  # never exit 5 —
                  "the previous value was replaced")  # the kit's own next pattern would trip a "kept" message otherwise
        differs = bool(kept)
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
    behind = False
    if check:
        # a mismatch (including no record at all — see `_recorded_state`) usually says the store's settings or
        # types predate the kit's — `findings` (exit 3) still wins, same as `differs` (exit 5, below), the
        # existing precedence stays intact
        behind, differs = _check_record(root)
    else:
        a = _ctx(ctx, store, "validate", "--changed", "--adopt", timeout=ADOPT_TIMEOUT)
        print("adopt: " + ((_lines(a.stdout) or ["ok"])[0] if a.returncode in (0, 3) else
                           (_lines(a.stderr) or [f"exit {a.returncode}"])[0]))
        if a.returncode not in (0, 3):
            return 2
        try:
            _write_adopt_digest(root, kept)  # the init/migrate/validate flow above actually ran: the record is current
        except OSError as e:
            print(f"warn: could not record the adopted digest under state/ ({e}) — adopt --check will keep "
                  "reading this store as behind")
    return 3 if findings else 6 if behind else 5 if differs else 0


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


def _ctx(ctx: Path, store: list[str], *args: str, timeout: float = HOOK_TIMEOUT) -> subprocess.CompletedProcess:
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


def _fit_lines(lines: list[str], budget: int) -> list[str]:
    """`lines` kept within `budget` bytes, ctx's own `brief`/`get` tail convention (its `_fit`, reimplemented here:
    this adapter re-budgets text ctx already answered, it does not call back into ctx for it): a trailing
    "… n more lines, raise --budget" marker for whatever does not fit, backing out already-kept lines if the
    marker itself would not fit otherwise."""
    kept: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        size = len(line.encode("utf-8")) + 1
        rest = len(lines) - index
        marker_len = len(f"… {rest} more lines, raise --budget".encode("utf-8"))
        if used + size + (marker_len + 1 if index < len(lines) - 1 else 0) > budget:
            note = f"… {rest} more lines, raise --budget"
            while kept and used + len(note.encode("utf-8")) + 1 > budget:
                used -= len(kept.pop().encode("utf-8")) + 1
                rest += 1
                note = f"… {rest} more lines, raise --budget"
            return kept + [note]
        kept.append(line)
        used += size
    return kept


def _split_brief_doc(raw: str) -> tuple[str, dict[str, str], str]:
    """One `ctx brief --session`/`brief <doc>` answer, split into its header line (the doc's key and type), its
    frontmatter as a dict in file order (the derived `sections: …` summary line included under that key), and
    the body that follows the blank separator line. ("", {}, "") on empty input."""
    lines = raw.splitlines()
    if not lines:
        return "", {}, ""
    i = 1
    fm: dict[str, str] = {}
    while i < len(lines) and lines[i]:
        key, sep, val = lines[i].partition(": ")
        if not sep or key not in SESSION_FM_LINE_KEYS:
            break
        fm[key] = val
        i += 1
    if i < len(lines) and not lines[i]:
        i += 1  # the blank separator before the body
    return lines[0], fm, "\n".join(lines[i:])


def _filtered_frontmatter(fm: dict[str, str]) -> list[str]:
    """`fm` (from `_split_brief_doc`) cut to `SESSION_BRIEF_KEEP`, in that order — the fields a 2026-10-01 gap
    report found a compaction's budget going to before the body ever got any (`stats`, `heartbeat`, plus
    `session_id`/`ref`/`updated` and the `sections` summary) are dropped outright, not merely shortened."""
    out = []
    for key in SESSION_BRIEF_KEEP:
        val = fm.get(key)
        if val:
            if key == "responsibilities" and len(val) > RESPONSIBILITIES_MAX:
                val = val[:RESPONSIBILITIES_MAX]
            out.append(f"{key}: {val}")
    return out


def _reorder_sections(body: str) -> str:
    """`body`'s `## ` sections moved so `SECTION_PRIORITY` leads, in that order, when present; every other
    section (there may be none, or none of the priority ones) keeps its original relative order after them."""
    parts = re.split(r"(?m)^(## .+)$", body)
    if len(parts) < 3:
        return body
    preamble = parts[0]
    pairs = list(zip(parts[1::2], parts[2::2]))

    def rank(index: int, heading: str) -> tuple[int, int]:
        name = heading[3:].strip()
        return (0, SECTION_PRIORITY.index(name)) if name in SECTION_PRIORITY else (1, index)
    ordered = sorted(enumerate(pairs), key=lambda kv: rank(kv[0], kv[1][0]))
    # a section moved off the end picks up the trailing newline `_doc_lines`'s own `body.strip("\n")` took off it
    return preamble + "".join(h + (t if t.endswith("\n") else t + "\n") for _, (h, t) in ordered)


def _resolve_epic_doc(ctx: Path, store: list[str], epic_key: str, remaining: Callable[[], float]) -> str | None:
    """The context doc `epic_key` (a session's `epic:` frontmatter field) names, through the store's own
    `resolve` rule (`ctx resolve`) — None when there is no key, the store sets no `resolve.key_regex`, the key
    matches no doc, or the call fails (a compact brief degrades, it does not go silent over one extra read).
    The kit's `resolve.fields` names no field: a field match would outrank the context doc's own
    `resolve.section` match (`Tracker & links`) — which is the only match that can ever apply, since no doc's
    frontmatter is ever searched by field — so `ctx resolve` always answers with the context doc the key's
    *Tracker & links* section names, never the session row that merely carries the key in its own `epic:`
    field. Less than 1.5s of `remaining()` left raises `subprocess.TimeoutExpired` rather than making the
    call, the same signal a call that actually timed out would raise — let through, not swallowed, so the
    caller (`_compact_brief`) tells a timeout, which it must report as skipped, from an ordinary no-match,
    which it must not."""
    if not epic_key:
        return None
    if remaining() < 1.5:
        raise subprocess.TimeoutExpired("ctx resolve", EPIC_LOOKUP_TIMEOUT)
    try:
        r = _ctx(ctx, store, "resolve", epic_key, timeout=min(EPIC_LOOKUP_TIMEOUT, remaining()))
    except OSError:
        return None
    if r.returncode != 0:
        return None
    row = _lines(r.stdout)
    doc = row[0].split(" · ", 1)[0].strip() if row else ""
    # a session row here means the store still carries the old `resolve.fields` (adopted before the kit
    # dropped it, not re-adopted since): no context doc is a truer answer than the session naming itself
    return doc if doc and not doc.startswith("sessions/") else None


def _epic_remaining_head(ctx: Path, store: list[str], doc_key: str, remaining: Callable[[], float]) -> list[str]:
    """The first `REMAINING_WORK_HEAD` lines of `doc_key`'s `## Remaining work` section, via `ctx get` (never a
    raw file read) — [] when the doc has no such section or the call fails; less than 1.5s of `remaining()`
    left raises `subprocess.TimeoutExpired` rather than making the call, the same signal a call that actually
    timed out would raise (see `_resolve_epic_doc`) — neither is swallowed here."""
    if remaining() < 1.5:
        raise subprocess.TimeoutExpired("ctx get", EPIC_LOOKUP_TIMEOUT)
    try:
        r = _ctx(ctx, store, "get", doc_key, "--section", "Remaining work", timeout=min(EPIC_LOOKUP_TIMEOUT, remaining()))
    except OSError:
        return []
    if r.returncode != 0:
        return []
    return r.stdout.splitlines()[:REMAINING_WORK_HEAD]


def _compact_brief(ctx: Path, store: list[str], payload: dict) -> None:
    """The `brief-session` hook's work: one owner line (`compacted — re-grounded from sessions/<name> and epic
    <key>`, the session row's own `epic:` frontmatter value verbatim, or `and no context doc` when the
    row carries no `epic:` field at all), then this session's brief with `BRIEF_BUDGET` spent on the body
    rather than on frontmatter the owner cannot act on — `ctx brief`'s own `--budget` has no way to drop
    frontmatter keys or reorder sections (ctx-store's own `brief` would need a field allow-list for that), so
    this re-budgets ctx's full answer itself. The owner line and the session brief are written and flushed to
    stdout before any context-doc lookup is even attempted, not merely before the slowest of them: neither
    needs anything beyond this session's own frontmatter, so a harness kill that lands during the slower
    lookups below still leaves both of these on stdout. Only once they are flushed does this try to resolve
    the context doc the `epic:` field names — up to two more ctx calls (`resolve`, `get`), each
    against a per-call deadline recomputed right before it, not one `min(EPIC_LOOKUP_TIMEOUT, remaining)`
    computed once and handed to two calls in a row (see `_resolve_epic_doc`/`_epic_remaining_head`). When a
    context doc resolved, its key and the head of its *Remaining work* follow in a separate `EPIC_BUDGET` —
    but only while more than 1.5s of the deadline remain before either group of lookups starts; skipped for
    lack of time, or cut off by a call that still timed out, one line `context doc: skipped (hook deadline)`
    stands in for that tail instead. The whole call has `COMPACT_DEADLINE` seconds, measured from entry; the
    session brief call itself gets `min(HOOK_TIMEOUT, remaining)`. Prints nothing when there is no session id
    on the hook payload, the deadline is already spent, or the session brief call itself fails or times out —
    a ctx call that times out never raises past this function, so whatever was already printed stays
    printed."""
    start = time.monotonic()

    def remaining() -> float:
        return COMPACT_DEADLINE - (time.monotonic() - start)

    sid = _session_id(payload)
    if not sid or remaining() <= 0:
        return
    try:
        r = _ctx(ctx, store, "brief", "--session", sid, "--full", "--budget", str(SESSION_RAW_BUDGET),
                 timeout=min(HOOK_TIMEOUT, remaining()))
    except subprocess.TimeoutExpired:
        return
    if r.returncode != 0:
        return
    header, fm, body = _split_brief_doc(r.stdout)
    if not header:
        return
    doc_key = header.split(" (", 1)[0].strip()
    epic_key = fm.get("epic", "")
    owner = f"compacted — re-grounded from {doc_key} and " + (f"epic {epic_key}" if epic_key else "no context doc")
    brief_lines = [header, *_filtered_frontmatter(fm)]
    reordered = _reorder_sections(body)
    if reordered.strip():
        brief_lines += ["", *reordered.split("\n")]
    sys.stdout.write("\n".join([owner, *_fit_lines(brief_lines, BRIEF_BUDGET)]) + "\n")
    sys.stdout.flush()

    epic_doc: str | None = None
    skipped = False
    if epic_key:
        if remaining() > 1.5:
            try:
                epic_doc = _resolve_epic_doc(ctx, store, epic_key, remaining)
            except subprocess.TimeoutExpired:
                skipped = True
        else:
            skipped = True
    tail: list[str] | None = None
    if epic_doc:
        if remaining() > 1.5:
            try:
                tail = [f"{epic_doc}:", *_epic_remaining_head(ctx, store, epic_doc, remaining)]
            except subprocess.TimeoutExpired:
                skipped = True
        else:
            skipped = True
    if tail:
        sys.stdout.write("\n" + "\n".join(_fit_lines(tail, EPIC_BUDGET)) + "\n")
        sys.stdout.flush()
    elif skipped:
        sys.stdout.write("\ncontext doc: skipped (hook deadline)\n")
        sys.stdout.flush()


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
        return r.stdout if r.returncode == 0 else ""
    if name == "brief-session":
        _compact_brief(ctx, store, payload)  # writes and flushes its own output; see its docstring
        return ""
    return ""


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
    sub.add_parser("pin", help="report the sha `install` found at the pinned location, and whether it verified")
    ap = sub.add_parser("adopt", help="make the content root a ctx store with the kit's settings (ctx init)")
    ap.add_argument("--check", action="store_true",
                     help="report only: exit 4 when not adopted, 3 on findings, 6 when the store's recorded "
                          "settings/types digest is stale or missing (behind)")
    ap.add_argument("--no-validate", action="store_true",
                     help="with --check: skip the whole-store `ctx validate` and only compare the recorded "
                          "digest — cheap, never touches ctx; invalid without --check")
    ap.add_argument("--replace", action="store_true", help="overwrite store files that differ from the kit's")
    sub.add_parser("mcp", help="run the ctx MCP server on the store (stdio)")
    mp = sub.add_parser("mcp-json", help="add the ctx MCP server to a project .mcp.json")
    mp.add_argument("file", type=Path)
    sub.add_parser("ctx", help="run one ctx verb on the store: ctx_adapter.py ctx <verb> [arguments]")
    hp = sub.add_parser("hook", help="run one Claude Code hook (hook JSON on stdin); always exit 0")
    hp.add_argument("name", choices=HOOKS)
    a = p.parse_args(argv)
    if a.cmd == "adopt" and a.no_validate and not a.check:
        ap.error("--no-validate requires --check")
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
        print(f"sha {CTX_SHA}")
        return 0
    if a.cmd == "where":
        ctx, why = resolve()
        if ctx is None:
            print(why, file=sys.stderr)
            return 1
        print(ctx)
        return 0
    if a.cmd == "pin":
        status = pin_status()
        if status is None:
            print(f"no sha record at the pinned location ({pinned_dir()}) — not installed, or installed before "
                  "this check existed; `ctx_adapter.py install` writes one with a fresh fetch only, so remove "
                  "that directory first when a copy is already there", file=sys.stderr)
            return 1
        print(f"pinned_sha {status.get('pinned_sha') or ''}")
        print(f"cloned_sha {status.get('cloned_sha') or ''}")
        print(f"verified {'true' if status.get('verified') else 'false'}")
        return 0
    if a.cmd == "mcp":
        return run_ctx(["mcp"], mcp=True)
    if a.cmd == "mcp-json":
        return mcp_json(a.file)
    try:
        if a.cmd == "adopt":
            return adopt(a.check, a.replace, a.no_validate)
        print(install())
    except (OSError, subprocess.SubprocessError) as e:
        print(f"ctx_adapter.py {a.cmd}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
