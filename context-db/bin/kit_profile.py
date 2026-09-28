#!/usr/bin/env python3
"""Config loader — the one place the engine and scripts learn which environment they run in.

One layer: the **env fact store** (`.context/reference/env/`, managed by `kb.py`, local to the
machine, never part of the kit). `config.json` holds the structural switches scripts branch on
(`environment` name, tracker.kind, github.org / review_bot, systems.*, domains, labels, diagrams, …);
the `<system>.md` tables hold the facts (channel ids, field ids, transitions, people). The tables
are projected onto the legacy dotted keys (`slack.channels`, `slack.repo_channels`,
`tracker.site/project/board_sprint_prefix/sprint_field/unplanned_field/transitions`,
`github.display_names`) so every `kit_profile.py get …` caller keeps working. Doc-template
overrides live in `<env store>/_templates/<type>.md`; the environment's prose is the context doc
`.context/reference/environment.md`, imported by the root CLAUDE.md.

The environment name is the store's `environment`, else `local`.

Usage from Python:      import kit_profile; cfg = kit_profile.load(); cfg["tracker"]["kind"]
Usage from shell:       python3 kit_profile.py                # environment name
                        python3 kit_profile.py env            # the env fact store directory
                        python3 kit_profile.py context        # the .context/ root
                        python3 kit_profile.py source         # env | none
                        python3 kit_profile.py get tracker.kind   # a dotted key (JSON for non-scalars)
                        python3 kit_profile.py domains        # extra .context domains, one per line
                        python3 kit_profile.py template epic  # the store's template override, or ""
                        python3 kit_profile.py zone           # the zone timestamps render in (UTC when WORKSPACE_TZ is unknown)
                        python3 kit_profile.py identity-env   # `export WORKSPACE_*=…` for identity set via plugin userConfig
                        python3 kit_profile.py session-env    # identity-env + CLAUDE_PROJECT_DIR — the plugin's SessionStart hook (#3)
                        python3 kit_profile.py workspace-rules  # WORKSPACE.md for the SessionStart hook to inject, or nothing (#3)
                        python3 kit_profile.py install-mode [--to-record]  # clone | plugin | dev-checkout (#34); --to-record: what setup.sh records
                        python3 kit_profile.py mode-hint kit_ref  # how a workspace shell names the kit in this mode (also workspace_md, makefile)
                        python3 kit_profile.py plugin         # {"repo","commit","version"} of a plugin install; exit 1 otherwise
                        python3 kit_profile.py gh-env         # `export NAME=value` for github.sandbox_token_prefix, or nothing
                        python3 kit_profile.py scratch [--stable] [sub]  # scratch dir, created: per session (0700, $XDG_RUNTIME_DIR/ai-baton-kit/ or <tmp>/ai-baton-kit-<uid>/; KIT_SCRATCH overrides; exit 2 on a symlinked/foreign root), or --stable per user (survives logout)
                        python3 kit_profile.py dir            # deprecated: always "" (kept for old callers)
Exit: 0 ok · 1 the thing asked about is absent (`get` of an unset key, `plugin` on a clone) · 2 usage or I/O error.
Below Python 3.9 this file exits with one line (`ai-baton needs Python 3.9+ (found …)`) before any other import.
Stdlib only; never prints anything from settings.local.json (`identity-env` re-exports plugin options only).
"""
from __future__ import annotations
import copy
import json
import os
import re
import shlex
import stat
import sys

MIN_PYTHON = (3, 9)  # zoneinfo (stdlib, imported below), Path.is_relative_to, str.removeprefix — the kit-wide floor


def python_floor_error(version_info=None) -> str | None:
    """None when `version_info` (default: this interpreter's `sys.version_info`) meets the kit's floor, else
    the one-line message a machine below it should stop with, before an import below fails with a stack trace
    instead. `version_info` takes any 2+-tuple starting (major, minor), so a test can pass a plain tuple."""
    major_minor = tuple((version_info if version_info is not None else sys.version_info)[:2])
    if major_minor >= MIN_PYTHON:
        return None
    found = ".".join(str(p) for p in (version_info if version_info is not None else sys.version_info)[:3])
    return f"ai-baton needs Python {'.'.join(str(p) for p in MIN_PYTHON)}+ (found {found})"


_PYTHON_FLOOR_ERROR = python_floor_error()
if _PYTHON_FLOOR_ERROR:
    sys.exit(_PYTHON_FLOOR_ERROR)

from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: E402

KIT = Path(__file__).resolve().parents[2]
KIT_AS_CALLED = Path(os.path.abspath(__file__)).parents[2]  # symlinks kept: a `.claude` link to a checkout is a clone
DEFAULT_NAME = "local"

# How the kit is installed (#34) and what follows from it — the one table every mode-dependent hint reads.
#   clone        — the workspace's `.claude/` (a git checkout, or a copy without git that sync.sh skips)
#   plugin       — a Claude Code plugin install: no git checkout, `.claude-plugin/plugin.json` (the plugin cache)
#   dev-checkout — any other kit copy: a git checkout not named `.claude` (a `.worktrees/kit_<topic>` worktree, a
#                  `claude --plugin-dir` checkout) — its own branch, so it updates with git, never `make claude_sync`
# kit_ref: how a hint names the kit for a shell in the workspace root (None = the kit's own absolute path — a plugin
# install and a dev checkout sit outside the workspace); workspace_md: `import` = the root CLAUDE.md imports
# `@.claude/WORKSPACE.md`, `hook` = the plugin's SessionStart hook injects it; makefile: the root Makefile does
# `include .claude/workspace.mk`; update: the command that brings the kit to the latest release ({kit} {plugin} {market}).
MODES: dict[str, dict] = {
    "clone": {"kit_ref": ".claude", "workspace_md": "import", "makefile": True,
              "update": "`make claude_sync` (or `sh $BATON/sync.sh`)"},
    "plugin": {"kit_ref": None, "workspace_md": "hook", "makefile": False,
               "update": "`claude plugin marketplace update {market} && claude plugin update {plugin}@{market}`, restart Claude Code"},
    "dev-checkout": {"kit_ref": None, "workspace_md": "hook", "makefile": False,
                     "update": "`git -C {kit} pull --ff-only` on its branch (a development checkout; `make claude_sync` "
                               "drives only the workspace's `.claude/` clone)"},
}


def install_mode(kit: Path | None = None) -> str:
    """`clone` | `plugin` | `dev-checkout` — the one rule (#34), used by setup.sh (`kit_profile.py install-mode`) and
    every Python caller: a git checkout is a clone when it is the workspace's `.claude/`, else a dev checkout; without
    git, `.claude/` is still a clone (a copy sync.sh skips), a dir with the plugin manifest is a plugin install, and
    anything else is a dev checkout."""
    kit = kit or KIT
    # the name the workspace uses, not a symlink's target — only for this very kit (a caller or test may point KIT
    # or `kit` at another copy, whose own name decides)
    name = KIT_AS_CALLED.name if KIT_AS_CALLED.resolve() == kit.resolve() else kit.name
    if (kit / ".git").exists():  # a directory, or a worktree's `.git` file
        return "clone" if name == ".claude" else "dev-checkout"
    if name == ".claude":
        return "clone"
    return "plugin" if (kit / ".claude-plugin" / "plugin.json").is_file() else "dev-checkout"


def mode_hint(key: str, mode: str | None = None, kit: Path | None = None, **fmt: str) -> str:
    """One entry of `MODES` for `mode` (default: this kit's), formatted: `kit_ref` resolves None to the kit's path,
    `update` fills `{kit}` and the caller's `{plugin}` / `{market}`."""
    kit = kit or KIT
    mode = mode or install_mode(kit)
    v = MODES[mode][key]
    if key == "kit_ref":
        return v or str(kit)
    return v.format(kit=kit, **fmt) if isinstance(v, str) else v


def mode_to_record(recorded: str, detected: str, root: Path) -> str:
    """The install mode the env store should hold: the detected one — except a dev checkout run inside a workspace
    whose recorded mode is `clone` and whose `.claude/` clone is still there (a kit worktree beside the installed
    kit), which keeps `clone`."""
    if detected == "dev-checkout" and recorded == "clone" and (root / ".claude" / ".git").exists():
        return "clone"
    return detected


def recorded_install_mode() -> str:
    """`kit.install_mode` as setup.sh recorded it in the env store, "" when not recorded (or no store)."""
    kit = env_config().get("kit")
    return str(kit.get("install_mode") or "").strip() if isinstance(kit, dict) else ""


def context_root() -> Path:
    """`CONTEXT_ROOT`, else the `.context/` beside the kit — also found from a kit worktree
    (`<root>/.worktrees/<name>/`), where the sibling is two levels up. On the plugin path the kit sits in
    Claude Code's plugin cache: `CLAUDE_PROJECT_DIR` (hooks get it, and the SessionStart hook re-exports it), else
    the nearest env store above the current directory — the Bash tool starts in the project dir but is not handed
    `CLAUDE_PROJECT_DIR` (#3). The walk-up is the last resort and a plugin install's only: it runs when neither
    variable is set, requires a real env store (`config.json`, not just a `.context/` dir), stops below `$HOME`, and
    the nearest store wins — as git picks the nearest repo. A clone never walks up (its store is beside the kit)."""
    env = os.environ.get("CONTEXT_ROOT", "").strip()
    if env:
        return Path(env)
    for base in (KIT.parent, KIT.parent.parent):
        if (base / ".context").is_dir():
            return base / ".context"
    proj = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if proj and (Path(proj) / ".context").is_dir():
        return Path(proj) / ".context"
    if install_mode(KIT) != "plugin":
        return KIT.parent / ".context"  # a checkout: its store is beside it or named by a variable, never guessed
    home = Path.home().resolve()
    try:
        cwd = Path.cwd().resolve()
    except OSError:  # the current directory was deleted under us
        return KIT.parent / ".context"
    for base in (cwd, *cwd.parents):
        if base == home:  # never `~/.context` (#68: the home dir is not a workspace root)
            break
        try:
            if (base / ".context" / "reference" / "env" / "config.json").is_file():
                return base / ".context"
        except OSError:  # an ancestor we may not stat (EACCES): not a workspace, keep walking
            continue
    return KIT.parent / ".context"


ENV_DIR = context_root() / "reference" / "env"
TEMPLATES_DIR = ENV_DIR / "_templates"  # `_`-prefixed: the index/verify walkers skip it



def section(cfg: dict, name: str) -> dict:
    """`cfg[name]` as an object: a missing, null or non-object section becomes `{}` (in this in-memory copy), so a reader
    never trips over a `null` that `config-set` or a hand edit left in config.json."""
    v = cfg.get(name)
    if not isinstance(v, dict):
        v = cfg[name] = {}
    return v

@lru_cache(maxsize=None)
def _env_config() -> dict:
    """config.json as written (cached, shared: never hand this object out — `env_config()` copies it)."""
    p = ENV_DIR / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SystemExit(f"{p}: invalid JSON — {e}; fix it (or move it aside and `python3 $BATON/context-db/bin/kb.py init --blank`)")
    except OSError as e:
        raise SystemExit(f"{p}: cannot read — {e}")


def env_config() -> dict:
    """config.json as written, `{}` when there is no store — a private copy per call, so a caller that edits it never
    changes what the next reader in the process sees. Invalid JSON is a one-line error naming the file, never a
    traceback in every importer."""
    return copy.deepcopy(_env_config())


env_config.cache_clear = _env_config.cache_clear  # type: ignore[attr-defined]  # the tests' reset hook, as before


@lru_cache(maxsize=None)
def _warn_no_store() -> None:
    """One stderr line per process when a script runs without an env store (the state env-init starts from)."""
    print(f"kit_profile: no env store at {ENV_DIR} — kit defaults apply (zone UTC, no ticket regex, no tracker tools); "
          "`python3 $BATON/context-db/bin/kb.py init --blank` creates one", file=sys.stderr)


def name() -> str:
    cfg = env_config()
    return str(cfg.get("environment") or "").strip() or DEFAULT_NAME


def available() -> list[str]:
    """The environments this machine knows: exactly one (kept for callers that iterate)."""
    return [name()] if env_config() else []


def directory() -> Path | None:
    """Deprecated — there is no prose/config directory in the kit any more; always None."""
    return None


def source() -> str:
    return "env" if env_config() else "none"


def _project_tables(cfg: dict) -> None:
    """Project the env tables onto the legacy dotted keys (in place)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import kb  # noqa: E402  (kb imports kit_profile for the root only)
        facts = kb.all_facts()
    except Exception:  # a malformed table must not take every script down
        return
    # every kind is read through its canonical name, so rows a session filed under a renamed heading
    # (`## channels` for `channel`) are visible until `kb.py migrate` moves them
    def rows(system: str, kind: str) -> dict:
        return kb.kind_rows(facts.get(system, {}), system, kind)
    def num(v: str):
        # the legacy keys held ints (transition ids); a digit string projects to int only when that round-trips —
        # `007` or `0123` is an id with meaningful zeros and stays a string
        return int(v) if v.isdigit() and str(int(v)) == v else v

    def vals(rows_: dict) -> dict:
        return {n: num(r["value"]) for n, r in rows_.items() if r["value"]}
    if rows("slack", "channel"):
        section(cfg, "slack")["channels"] = {**(section(cfg, "slack").get("channels") or {}), **vals(rows("slack", "channel"))}
    if rows("slack", "review-venue"):
        section(cfg, "slack")["repo_channels"] = {**(section(cfg, "slack").get("repo_channels") or {}), **vals(rows("slack", "review-venue"))}
    for k, v in vals(rows("tracker", "setting")).items():
        section(cfg, "tracker")[k] = v
    fields = vals(rows("tracker", "field"))
    for nm, key in (("sprint", "sprint_field"), ("unplanned", "unplanned_field")):
        if nm in fields:
            section(cfg, "tracker")[key] = fields[nm]
    if rows("tracker", "transition"):
        section(cfg, "tracker")["transitions"] = {**(section(cfg, "tracker").get("transitions") or {}), **vals(rows("tracker", "transition"))}
    if rows("github", "person"):
        section(cfg, "github")["display_names"] = {**(section(cfg, "github").get("display_names") or {}), **vals(rows("github", "person"))}


def load(strict: bool = True) -> dict:
    """The merged config: env/config.json with the fact tables projected onto the legacy keys. With no store: `strict`
    (the default — a script that needs the configuration) raises a one-line SystemExit; `strict=False` (what
    `get()`, `tz()`, `zone()` use, so the session scripts and the heartbeat run before env-init) returns `{}`
    after ONE stderr warning per process. Each call returns a private copy of the cached merge: a caller may edit it."""
    return copy.deepcopy(_load(strict))


@lru_cache(maxsize=None)
def _load(strict: bool = True) -> dict:
    """The cached merge behind `load()`, built once from a deep copy of config.json — the projection and the alias
    block below edit this copy, never the parsed file. Read-only for `get()`; everyone else goes through `load()`."""
    cfg = {k: v for k, v in copy.deepcopy(_env_config()).items() if not k.startswith("_")}
    if not cfg:
        if strict:
            raise SystemExit(f"no configuration: no env store at {ENV_DIR} — run "
                             f"`python3 $BATON/context-db/bin/kb.py init --blank`")
        _warn_no_store()
        return {}
    _project_tables(cfg)
    # a store not yet migrated (`kb.py migrate`) still answers under the renamed flag
    sysf = cfg.get("systems") or {}
    for old, new in (("snowflake", "datalake"),):
        if old in sysf and new not in sysf:
            sysf[new] = sysf[old]
    cfg["environment"] = name()
    cfg["_dir"] = ""
    cfg["_env"] = str(ENV_DIR)
    return cfg


load.cache_clear = _load.cache_clear  # type: ignore[attr-defined]  # the tests' reset hook, as before


def domains() -> list[str]:
    out: list[str] = []
    for dom in env_config().get("domains") or []:
        if dom and dom not in out:
            out.append(dom)
    return out


def get(path: str, default=None):
    """Dotted lookup: get('tracker.key_regex'). Without an env store every key is `default` (one warning)."""
    cur: object = _load(strict=False)  # walk the cached merge; an object or list is copied on the way out
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return copy.deepcopy(cur) if isinstance(cur, (dict, list)) else cur


# Identity: the user's own values, never the environment's. Two sources, one reader. On the plugin path
# Claude Code collects them through `plugin.json` `userConfig` and hands them to hooks as
# `CLAUDE_PLUGIN_OPTION_<KEY>`; on the clone path they are the `WORKSPACE_*` env of the ignored
# `.claude/settings.local.json`. `identity()` prefers the plugin option, so a value typed into `/config` wins
# over a stale file, and every script keeps reading `WORKSPACE_*` through it.
IDENTITY_KEYS: dict[str, str] = {  # WORKSPACE_* variable → userConfig key
    "WORKSPACE_USER": "user_name",
    "WORKSPACE_GITHUB_LOGIN": "github_login",
    "WORKSPACE_TZ": "tz",
    "WORKSPACE_SLACK_SELF_DM": "slack_self_dm",
    "WORKSPACE_SLACK_LATTICE_DM": "slack_lattice_dm",
}
OPTION_PREFIX = "CLAUDE_PLUGIN_OPTION_"


def option_var(var: str) -> str:
    """The env variable Claude Code sets for the plugin option that carries `var` (`WORKSPACE_TZ` → `CLAUDE_PLUGIN_OPTION_TZ`)."""
    return OPTION_PREFIX + IDENTITY_KEYS[var].upper()


def identity(var: str, environ: dict | None = None) -> str:
    """The identity value for a `WORKSPACE_*` variable: the plugin option first, then the variable itself
    (settings.local.json env, or a shell export), else "". Unknown variables read the environment only."""
    env = os.environ if environ is None else environ
    if var in IDENTITY_KEYS:
        v = str(env.get(option_var(var), "")).strip()
        if v:
            return v
    return str(env.get(var, "")).strip()


def identity_env(environ: dict | None = None) -> dict[str, str]:
    """`{WORKSPACE_*: value}` for every identity variable whose plugin option is set — what a hook exports so
    scripts and skill bodies that read `$WORKSPACE_*` see the `/config` values on the plugin path. A variable
    already set in the environment (settings.local.json) is included only when the option is set: the file
    keeps working unchanged, and the option wins."""
    env = os.environ if environ is None else environ
    return {var: str(env.get(option_var(var), "")).strip() for var in IDENTITY_KEYS if str(env.get(option_var(var), "")).strip()}


def identity_source(var: str, environ: dict | None = None) -> str:
    """Where `identity(var)` comes from: `option` | `env` | `` (unset). Never the value."""
    env = os.environ if environ is None else environ
    if var in IDENTITY_KEYS and str(env.get(option_var(var), "")).strip():
        return "option"
    return "env" if str(env.get(var, "")).strip() else ""


def tz() -> str:
    """Owner's display zone: WORKSPACE_TZ (plugin option, else settings.local.json), else the configured default, else UTC."""
    return identity("WORKSPACE_TZ") or str(get("tz_default", "UTC"))


@lru_cache(maxsize=None)
def zone() -> tuple[ZoneInfo, str]:
    """`tz()` as a ZoneInfo, guarded: `(ZoneInfo(tz()), "")` for a known zone; for an unknown one
    `(ZoneInfo("UTC"), " (UTC — WORKSPACE_TZ '<name>' unknown)")` plus ONE stderr line per process
    (cached), so a typo in settings.local.json never kills every importer — callers render in UTC and
    append the note where they show a timestamp."""
    name = tz()
    try:
        return ZoneInfo(name), ""
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):  # OSError: a directory-shaped name (`Europe`) on older tzdata
        print(f"kit_profile: WORKSPACE_TZ {name!r} is not a known zone, rendering in UTC", file=sys.stderr)
        return ZoneInfo("UTC"), f" (UTC — WORKSPACE_TZ {name!r} unknown)"


def template(kind: str) -> Path | None:
    """The env store's override for a doc template (`<env>/_templates/<kind>.md`), if it ships one."""
    t = TEMPLATES_DIR / f"{kind}.md"
    return t if t.is_file() else None


def _owning_pid() -> str | None:
    """The pid of the Claude process that owns this shell: `CLAUDE_PID` when the harness exports it, else the
    first ancestor whose comm is `claude`, `node` or a bare version string (the desktop app names its binary
    that way) — the same idea as `session-register/heartbeat.sh`'s owner walk, which today honours neither
    `CLAUDE_PID` nor a version-string comm (#47). None when nothing matches (not Linux,
    or a shell not started by Claude)."""
    if os.environ.get("CLAUDE_PID"):
        return os.environ["CLAUDE_PID"]
    pid = os.getpid()
    for _ in range(64):
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        if comm in ("claude", "node") or re.fullmatch(r"\d+\.\d+\.\d+[\w.-]*", comm):
            return str(pid)
        ppid = int(stat.rsplit(")", 1)[1].split()[1])   # field 4, after the parenthesised comm
        if ppid <= 1:
            return None
        pid = ppid
    return None


def scratch(sub: str | None = None, stable: bool = False) -> Path:
    """A scratch directory that exists on every machine (created on every call — never cached).

    Sandboxes export `CLAUDE_JOB_DIR` (per job); a host does not, so a path written as `<job dir>/tmp/x` collapsed
    into `/tmp/x` there and sessions overwrote each other's hand-off prompts and reports.

    Per-session (default): `<CLAUDE_JOB_DIR>/tmp` → `$KIT_SCRATCH` (explicit override) →
    `$XDG_RUNTIME_DIR/ai-baton-kit/<key>`, else `<$TMPDIR or /tmp>/ai-baton-kit-<uid>/<key>` where the key is `CLAUDE_CODE_SESSION_ID`, else `pid-<owning claude pid>`
    (`CLAUDE_PID` when the harness exports it, otherwise found by walking the process ancestry — shared by every
    Bash call and fork of one session), else `uid-<uid>` (per user: two sessions of one user then share it,
    which is still never a collision between machines or users — callers that need continuity across that
    case pass a path explicitly). The immediate ppid is NOT used: `$(python3 … scratch)` runs under a fresh
    shell each time, so it would be per invocation.

    Stable (`stable=True`, CLI `scratch --stable <sub>`): a per-user location that outlives the session and
    the login — `<CLAUDE_JOB_DIR>/tmp/<sub>` in a sandbox, else `${XDG_CACHE_HOME:-~/.cache}/ai-baton-kit/<sub>` —
    for tooling that keeps run history or a `latest` pointer across sweeps (pr-scan).
    `sub` appends one component.

    The per-user root under a runtime or temp dir (`ai-baton-kit[-<uid>]`) is created `0700` and must be a real
    directory owned by the caller — a symlink or another user's directory raises `ScratchError` (CLI: exit 2), since
    on a shared host another user could pre-create it and read or redirect review bundles and PR bodies (#138).
    `CLAUDE_JOB_DIR` and `KIT_SCRATCH` are the caller's own choice and are not checked."""
    job = os.environ.get("CLAUDE_JOB_DIR")
    if stable:
        base = Path(job) / "tmp" if job else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ai-baton-kit"
    elif job:
        base = Path(job) / "tmp"
    elif os.environ.get("KIT_SCRATCH"):
        base = Path(os.environ["KIT_SCRATCH"])
    else:
        xdg = os.environ.get("XDG_RUNTIME_DIR")  # per user and 0700 already
        root = Path(xdg) / "ai-baton-kit" if xdg else \
            Path(os.environ.get("TMPDIR") or "/tmp") / f"ai-baton-kit-{os.getuid()}"  # shared temp root: one dir per uid
        private_dir(root)
        owner = None if os.environ.get("CLAUDE_CODE_SESSION_ID") else _owning_pid()
        key = os.environ.get("CLAUDE_CODE_SESSION_ID") or (f"pid-{owner}" if owner else f"uid-{os.getuid()}")
        base = root / re.sub(r"[^\w.-]", "_", key)
        base.mkdir(mode=0o700, exist_ok=True)  # `parents=True` below would give it the umask's mode
    if sub:
        base = base / sub
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return base


class ScratchError(RuntimeError):
    """The scratch root is not a private directory of this user."""


def private_dir(path: Path) -> Path:
    """`path` as a `0700` directory owned by this user: created when missing; an existing one must be a real
    directory (not a symlink) owned by the caller, and loses any group/other permission bits."""
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ScratchError(f"scratch root {path} is a symlink or not a directory — remove it or set KIT_SCRATCH")
    if st.st_uid != os.getuid():
        raise ScratchError(f"scratch root {path} belongs to uid {st.st_uid}, not {os.getuid()} — set KIT_SCRATCH")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


@lru_cache(maxsize=1)
def gh_logged_in() -> bool:
    """True when `gh` holds a usable github.com token of its own — `gh auth token` succeeds with every token
    variable removed (keyring or hosts.yml; the token itself is discarded, never printed). A hosts.yml copied
    into a sandbox without its keyring does not count, so a sandbox keeps its placeholder."""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")}
    try:
        import subprocess
        r = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"], env=env,
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def configured_token_prefix() -> tuple[str, str] | None:
    """`github.sandbox_token_prefix` as configured, split into (NAME, value); None when empty or no store."""
    if not env_config():
        return None
    pref = str(get("github.sandbox_token_prefix", "") or "").strip()
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.+)$", pref)
    return (m.group(1), m.group(2)) if m else None


def gh_token_prefix() -> tuple[str, str] | None:
    """The configured prefix, unless `gh` is logged in natively here. A sandbox whose proxy injects the real
    token sets a placeholder (`gh` refuses to run without one); a host sharing that store keeps its real
    login, since a placeholder there would override it. A native login beats the configured prefix."""
    p = configured_token_prefix()
    return None if p is None or gh_logged_in() else p


def gh_env(base: dict | None = None) -> dict:
    """A copy of `base` (default os.environ) with the token prefix applied unless that variable is set."""
    env = dict(os.environ if base is None else base)
    p = gh_token_prefix()
    if p and not env.get(p[0]):
        env[p[0]] = p[1]
    return env


def plugin_install(kit: Path | None = None, environ: dict | None = None) -> dict[str, str] | None:
    """`{repo, commit, version, updated}` when the kit is a Claude Code plugin install (no git checkout: the plugin
    cache), None on a clone. `repo` = `owner/repo` from plugin.json `repository`; `commit` and `updated` = the
    `gitCommitSha` and `lastUpdated` (ISO, else `installedAt`) Claude Code recorded for this install path in
    `installed_plugins.json` ("" when not recorded); `version` = plugin.json's. Lets kit-health name the installed kit
    where git cannot (#3) and notice a hand edit in the cache (#11)."""
    kit = kit or KIT
    env = os.environ if environ is None else environ
    if install_mode(kit) != "plugin":
        return None
    try:
        manifest = json.loads((kit / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", str(manifest.get("repository") or ""))
    out = {"repo": m.group(1) if m else "", "commit": "", "version": str(manifest.get("version") or ""), "updated": ""}
    # <config>/plugins/cache/<marketplace>/<plugin>/<version> — the registry sits in <config>/plugins/
    registries = [kit.parents[3] / "installed_plugins.json"] if len(kit.parents) > 3 and kit.parents[2].name == "cache" else []
    config = env.get("CLAUDE_CONFIG_DIR", "").strip()
    registries.append((Path(config) if config else Path.home() / ".claude") / "plugins" / "installed_plugins.json")
    for reg in registries:
        try:
            plugins = json.loads(reg.read_text(encoding="utf-8")).get("plugins") or {}
        except (OSError, ValueError, AttributeError):
            continue
        for entries in plugins.values():
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and e.get("installPath") and Path(e["installPath"]).resolve() == kit.resolve():
                    out["commit"] = str(e.get("gitCommitSha") or "")
                    out["updated"] = str(e.get("lastUpdated") or e.get("installedAt") or "")
                    return out
    return out


def session_env(environ: dict | None = None) -> dict[str, str]:
    """What the plugin's SessionStart hook exports into `$CLAUDE_ENV_FILE` so every later Bash command sees it: the
    identity options (`identity_env`) plus `CLAUDE_PROJECT_DIR`, which hooks get and the Bash tool does not — without
    it the engine cannot find the workspace's `.context/` from a plugin install — and `BATON`, the kit root that skill
    bodies call the engine through (`$BATON/context-db/bin/…`) (#3)."""
    env = os.environ if environ is None else environ
    out = identity_env(env)
    proj = str(env.get("CLAUDE_PROJECT_DIR", "")).strip()
    if proj:
        out["CLAUDE_PROJECT_DIR"] = proj
    root = str(env.get("CLAUDE_PLUGIN_ROOT", "")).strip()
    if root:  # `$BATON/…` in skill bodies = the kit on both paths; a clone sets BATON=.claude in settings.json (#3)
        out["BATON"] = root
    return out


def workspace_rules(environ: dict | None = None, kit: Path | None = None) -> str:
    """The kit's always-on body (`WORKSPACE.md`) for the plugin's SessionStart hook to print into the session's
    context — a plugin cannot ship a CLAUDE.md, and on a plugin install the seeded `@.claude/WORKSPACE.md` import has
    no file to load (#3). "" unless `CLAUDE_PROJECT_DIR` is, or sits below, a kit workspace (a dir holding an env store
    — the plugin is installed per user, so other projects get nothing; a repo inside the workspace gets it, as a clone's
    parent CLAUDE.md reaches it) that does not import its own copy (a `.claude/` clone)."""
    env = os.environ if environ is None else environ
    kit = kit or KIT
    proj = str(env.get("CLAUDE_PROJECT_DIR", "")).strip()
    if not proj:
        return ""
    start, home = Path(proj).resolve(), Path.home().resolve()
    root = None
    for base in (start, *start.parents):
        if base == home:  # never `~/.context` (#68)
            break
        if (base / ".context" / "reference" / "env").is_dir():
            root = base
            break
    if root is None or (root / ".claude" / "WORKSPACE.md").is_file():
        return ""
    return (kit / "WORKSPACE.md").read_text(encoding="utf-8")  # unreadable in a kit workspace = broken: raise, the hook says so


NEEDS_ARG = {"template": "<type>", "identity-source": "<WORKSPACE_* variable>", "get": "<dotted.config.key>"}


def main(argv: list[str]) -> int:
    """Exit codes (docs/engine-cli.md): 0 ok · 1 the thing asked about is absent (`get`, `plugin`) · 2 usage / I/O error."""
    cmd = argv[1] if len(argv) > 1 else "name"
    if cmd in NEEDS_ARG and len(argv) < 3:
        print(f"kit_profile: `{cmd}` needs {NEEDS_ARG[cmd]} — kit_profile.py {cmd} {NEEDS_ARG[cmd]}", file=sys.stderr)
        return 2
    if cmd == "name":
        print(name())
    elif cmd == "dir":
        print("")
    elif cmd == "env":
        print(ENV_DIR)
    elif cmd == "context":
        print(context_root())
    elif cmd == "source":
        print(source())
    elif cmd == "list":
        print("\n".join(available()))
    elif cmd == "domains":
        print("\n".join(domains()))
    elif cmd == "tz":
        print(tz())
    elif cmd == "zone":
        print(zone()[0].key)  # the zone actually rendered — UTC when WORKSPACE_TZ is unknown
    elif cmd == "template":
        t = template(argv[2])
        print(t or "")
    elif cmd == "scratch":
        rest = [a for a in argv[2:] if a != "--stable"]
        try:
            print(scratch(rest[0] if rest else None, stable="--stable" in argv[2:]))
        except ScratchError as e:
            print(f"kit_profile: {e}", file=sys.stderr)
            return 2
    elif cmd == "identity-env":
        # for a SessionStart hook / shell callers: eval "$(python3 kit_profile.py identity-env)" — prints an export line per
        # identity value set through plugin userConfig, nothing otherwise (settings.local.json is never echoed)
        for var, value in identity_env().items():
            print(f"export {var}={shlex.quote(value)}")
    elif cmd == "session-env":
        # the plugin's SessionStart hook: identity-env plus CLAUDE_PROJECT_DIR (#3)
        for var, value in session_env().items():
            print(f"export {var}={shlex.quote(value)}")
    elif cmd == "workspace-rules":
        # the plugin's SessionStart hook: WORKSPACE.md on stdout (→ the session's context) for a plugin-path workspace (#3)
        sys.stdout.write(workspace_rules())
    elif cmd == "install-mode":
        # clone | plugin | dev-checkout (#34); --to-record: the mode setup.sh writes to `kit.install_mode`
        detected = install_mode()
        if "--to-record" in argv[2:]:
            print(mode_to_record(recorded_install_mode(), detected, context_root().parent))
        else:
            print(detected)
    elif cmd == "mode-hint":
        # one MODES entry for this kit's mode: kit_ref (how a workspace shell names the kit) | workspace_md | makefile
        v = mode_hint(argv[2]) if len(argv) > 2 and argv[2] in ("kit_ref", "workspace_md", "makefile") else None
        if v is None:
            print("mode-hint: kit_ref | workspace_md | makefile", file=sys.stderr)
            return 2
        print(str(v).lower() if isinstance(v, bool) else v)
    elif cmd == "plugin":
        # `repo commit version` of a plugin install, nothing (exit 1) on a clone
        p = plugin_install()
        if p is None:
            return 1
        print(json.dumps(p))
    elif cmd == "identity-source":
        # `option` | `env` | `` for one WORKSPACE_* variable — the source, never the value
        print(identity_source(argv[2]))
    elif cmd == "gh-env":
        # for shell callers: eval "$(python3 kit_profile.py gh-env)" — prints nothing when there is no prefix
        p = gh_token_prefix()
        if p and not os.environ.get(p[0]):
            print(f"export {p[0]}={shlex.quote(p[1])}")
    elif cmd == "get":
        v = get(argv[2])
        if v is None:
            return 1
        print(v if isinstance(v, (str, int, float)) and not isinstance(v, bool) else json.dumps(v))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


def cli(argv: list[str]) -> int:
    """main() with the exit-code contract applied: a `SystemExit("<message>")` from the library (an unreadable or invalid
    store) is one stderr line and exit 2, not Python's default exit 1 for a message."""
    try:
        return main(argv)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv))
