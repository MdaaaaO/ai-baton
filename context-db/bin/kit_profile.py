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
                        python3 kit_profile.py identity-env   # `export WORKSPACE_*=…` for identity set via plugin userConfig (#116)
                        python3 kit_profile.py session-env    # identity-env + CLAUDE_PROJECT_DIR — the plugin's SessionStart hook (#3)
                        python3 kit_profile.py workspace-rules  # WORKSPACE.md for the SessionStart hook to inject, or nothing (#3)
                        python3 kit_profile.py plugin         # {"repo","commit","version"} of a plugin install; exit 1 on a clone
                        python3 kit_profile.py gh-env         # `export NAME=value` for github.sandbox_token_prefix, or nothing
                        python3 kit_profile.py scratch [--stable] [sub]  # scratch dir, created: per session, or --stable per user (survives logout)
                        python3 kit_profile.py dir            # deprecated: always "" (kept for old callers)
Stdlib only; never prints anything from settings.local.json (`identity-env` re-exports plugin options only).
"""
from __future__ import annotations
import json
import os
import re
import shlex
import sys
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KIT = Path(__file__).resolve().parents[2]
DEFAULT_NAME = "local"


def context_root() -> Path:
    """`CONTEXT_ROOT`, else the `.context/` beside the kit — also found from a kit worktree
    (`<root>/.worktrees/<name>/`), where the sibling is two levels up. On the plugin path (#118) the kit sits in
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
    if (KIT / ".git").exists() or not (KIT / ".claude-plugin" / "plugin.json").is_file():
        return KIT.parent / ".context"  # a checkout: its store is beside it or named by a variable, never guessed
    home = Path.home().resolve()
    try:
        cwd = Path.cwd().resolve()
    except OSError:  # the current directory was deleted under us
        return KIT.parent / ".context"
    for base in (cwd, *cwd.parents):
        if base == home:  # never `~/.context` (#168: the home dir is not a workspace root)
            break
        if (base / ".context" / "reference" / "env" / "config.json").is_file():
            return base / ".context"
    return KIT.parent / ".context"


ENV_DIR = context_root() / "reference" / "env"
TEMPLATES_DIR = ENV_DIR / "_templates"  # `_`-prefixed: the index/verify walkers skip it


@lru_cache(maxsize=None)
def env_config() -> dict:
    """config.json as written, `{}` when there is no store. Invalid JSON is a one-line error naming the file (exit 1),
    never a traceback in every importer (#59 E16)."""
    p = ENV_DIR / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SystemExit(f"{p}: invalid JSON — {e}; fix it (or move it aside and `python3 .claude/context-db/bin/kb.py init --blank`)")
    except OSError as e:
        raise SystemExit(f"{p}: cannot read — {e}")


@lru_cache(maxsize=None)
def _warn_no_store() -> None:
    """One stderr line per process when a script runs without an env store (the state env-init starts from)."""
    print(f"kit_profile: no env store at {ENV_DIR} — kit defaults apply (zone UTC, no ticket regex, no tracker tools); "
          "`python3 .claude/context-db/bin/kb.py init --blank` creates one", file=sys.stderr)


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
        # `007` or `0123` is an id with meaningful zeros and stays a string (#58 E15)
        return int(v) if v.isdigit() and str(int(v)) == v else v

    def vals(rows_: dict) -> dict:
        return {n: num(r["value"]) for n, r in rows_.items() if r["value"]}
    if rows("slack", "channel"):
        cfg.setdefault("slack", {})["channels"] = {**cfg.get("slack", {}).get("channels", {}), **vals(rows("slack", "channel"))}
    if rows("slack", "review-venue"):
        cfg.setdefault("slack", {})["repo_channels"] = {**cfg.get("slack", {}).get("repo_channels", {}), **vals(rows("slack", "review-venue"))}
    for k, v in vals(rows("tracker", "setting")).items():
        cfg.setdefault("tracker", {})[k] = v
    fields = vals(rows("tracker", "field"))
    for nm, key in (("sprint", "sprint_field"), ("unplanned", "unplanned_field")):
        if nm in fields:
            cfg.setdefault("tracker", {})[key] = fields[nm]
    if rows("tracker", "transition"):
        cfg.setdefault("tracker", {})["transitions"] = {**cfg.get("tracker", {}).get("transitions", {}), **vals(rows("tracker", "transition"))}
    if rows("github", "person"):
        cfg.setdefault("github", {})["display_names"] = {**cfg.get("github", {}).get("display_names", {}), **vals(rows("github", "person"))}


@lru_cache(maxsize=None)
def load(strict: bool = True) -> dict:
    """The merged config: env/config.json with the fact tables projected onto the legacy keys. With no store: `strict`
    (the default — a script that needs the configuration) raises a one-line SystemExit; `strict=False` (what
    `get()`, `tz()`, `zone()` use, so the session scripts and the heartbeat run before env-init) returns `{}`
    after ONE stderr warning per process (#59 E08)."""
    cfg = {k: v for k, v in env_config().items() if not k.startswith("_")}
    if not cfg:
        if strict:
            raise SystemExit(f"no configuration: no env store at {ENV_DIR} — run "
                             f"`python3 .claude/context-db/bin/kb.py init --blank`")
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


def domains() -> list[str]:
    out: list[str] = []
    for dom in env_config().get("domains") or []:
        if dom and dom not in out:
            out.append(dom)
    return out


def get(path: str, default=None):
    """Dotted lookup: get('tracker.key_regex'). Without an env store every key is `default` (one warning)."""
    cur: object = load(strict=False)
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# Identity (#116): the user's own values, never the environment's. Two sources, one reader. On the plugin path
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
    `CLAUDE_PID` nor a version-string comm (#68). None when nothing matches (not Linux,
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
    `<runtime tmp>/ai-baton-kit/<key>` where the key is `CLAUDE_CODE_SESSION_ID`, else `pid-<owning claude pid>`
    (`CLAUDE_PID` when the harness exports it, otherwise found by walking the process ancestry — shared by every
    Bash call and fork of one session), else `uid-<uid>` (per user: two sessions of one user then share it,
    which is still never a collision between machines or users — callers that need continuity across that
    case pass a path explicitly). The immediate ppid is NOT used: `$(python3 … scratch)` runs under a fresh
    shell each time, so it would be per invocation.

    Stable (`stable=True`, CLI `scratch --stable <sub>`): a per-user location that outlives the session and
    the login — `<CLAUDE_JOB_DIR>/tmp/<sub>` in a sandbox, else `${XDG_CACHE_HOME:-~/.cache}/ai-baton-kit/<sub>` —
    for tooling that keeps run history or a `latest` pointer across sweeps (pr-scan).
    `sub` appends one component."""
    job = os.environ.get("CLAUDE_JOB_DIR")
    if stable:
        base = Path(job) / "tmp" if job else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ai-baton-kit"
    elif job:
        base = Path(job) / "tmp"
    elif os.environ.get("KIT_SCRATCH"):
        base = Path(os.environ["KIT_SCRATCH"])
    else:
        run = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
        owner = None if os.environ.get("CLAUDE_CODE_SESSION_ID") else _owning_pid()
        key = os.environ.get("CLAUDE_CODE_SESSION_ID") or (f"pid-{owner}" if owner else f"uid-{os.getuid()}")
        base = Path(run) / "ai-baton-kit" / re.sub(r"[^\w.-]", "_", key)
    if sub:
        base = base / sub
    base.mkdir(parents=True, exist_ok=True)
    return base


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
    """`{repo, commit, version}` when the kit is a Claude Code plugin install (no git checkout: the plugin cache),
    None on a clone. `repo` = `owner/repo` from plugin.json `repository`; `commit` = the `gitCommitSha` Claude Code
    recorded for this install path in `installed_plugins.json` ("" when not recorded); `version` = plugin.json's.
    Lets kit-health name the installed kit where git cannot (#3)."""
    kit = kit or KIT
    env = os.environ if environ is None else environ
    if (kit / ".git").exists():
        return None
    try:
        manifest = json.loads((kit / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", str(manifest.get("repository") or ""))
    out = {"repo": m.group(1) if m else "", "commit": "", "version": str(manifest.get("version") or "")}
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
                    return out
    return out


def session_env(environ: dict | None = None) -> dict[str, str]:
    """What the plugin's SessionStart hook exports into `$CLAUDE_ENV_FILE` so every later Bash command sees it: the
    identity options (`identity_env`) plus `CLAUDE_PROJECT_DIR`, which hooks get and the Bash tool does not — without
    it the engine cannot find the workspace's `.context/` from a plugin install (#3)."""
    env = os.environ if environ is None else environ
    out = identity_env(env)
    proj = str(env.get("CLAUDE_PROJECT_DIR", "")).strip()
    if proj:
        out["CLAUDE_PROJECT_DIR"] = proj
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
        if base == home:  # never `~/.context` (#168)
            break
        if (base / ".context" / "reference" / "env").is_dir():
            root = base
            break
    if root is None or (root / ".claude" / "WORKSPACE.md").is_file():
        return ""
    try:
        return (kit / "WORKSPACE.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "name"
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
        print(scratch(rest[0] if rest else None, stable="--stable" in argv[2:]))
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
