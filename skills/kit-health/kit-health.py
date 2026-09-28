#!/usr/bin/env python3
"""kit-health.py — is the kit sound everywhere it runs, and wired correctly on THIS machine?

Deterministic part of the `kit-health` skill (the judgement part — re-reading flagged skills — is the
skill's Sonnet fork). Prints a markdown report; exit 0 = green, 1 = warnings only, 2 = errors.

  python3 $BATON/skills/kit-health/kit-health.py [--stale N] [--stamp] [--report FILE] [--quiet] [--ci]

Sections:
  1. kit        — kit_verify (frontmatter + env store), stale units, install mode vs the recorded one (#34),
                  git state (sync-check), newer kit release (#33)
  2. leaks      — environment-specific values anywhere in the kit (every skill, agent, engine file, doc,
                  `.github/`): generic SHAPES (Slack ids, custom-field ids, ticket keys, account ids, hosts,
                  tz literals, memory-note pointers) plus every literal VALUE this environment has configured
                  (the env store's tables + config.json: org, repos, domains) and the user's identity
                  (WORKSPACE_USER / WORKSPACE_GITHUB_LOGIN from the plugin options or settings.local.json — matched, never printed).
                  No unit is exempt: the kit never names an environment, so no unit owns its values.
  3. config     — the env fact store (`.context/reference/env/`): config completeness, renamed flags,
                  a retired capability key (`slack.enabled`, `github.signed_commits`) still in config.json
  4. machine    — this machine's wiring: environment name, CLAUDE.md imports, Makefile include, memory
                  symlink, pr-review config vs github.org, required CLIs, systems.* reachable from a shell;
                  one legacy line: a leftover `.claude/profiles/` clone (the layer retired 2026-09-25) → delete it
  5. engine     — smoke: verify + index on the live `.context/`, kit_profile.py from the env store, new.sh
                  scaffolds every doc type into a scratch content root; the ctx-store pin: `.context/` adopted,
                  and the pinned `ctx --version` answers the API the adapter expects
  6. stamp      — last green run of THIS environment: `.context/kit-health/HEALTH-<env>.md` (local; each
                  machine keeps its own; records kit_commit, kit_version and install_mode)
--stamp writes that HEALTH file (kit_commit) when there are no errors and no un-accepted leak hit (other
warnings are recorded, not fatal). Every reported value is redacted to `<kind>:<first2>…`.
Stdlib only. Never prints an identity value (plugin option or settings.local.json).
"""
from __future__ import annotations
import argparse
import datetime as dt
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
LEGACY_PROFILES = KIT / "profiles"  # pre-2026-09-25 profiles clone — must be gone (gitignored so a leftover never blocks a pull)
BIN = KIT / "context-db" / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402
import kb  # noqa: E402
import frontmatter as fmt  # noqa: E402
import leak_shapes  # noqa: E402

CTX: Path | None = None   # overrides for a caller (a test) that names the workspace outright; None = resolve per call
ROOT: Path | None = None


def ctx() -> Path:
    """The workspace's `.context/`, resolved when a section runs, not at import (#91): the `CTX` override, else
    `CONTEXT_ROOT`, else `kit_profile.context_root()` for the kit this script ships in, else the `.context/` beside a kit
    a caller pointed `KIT` at (a test's fixture), so a fixture never reads the developer's live workspace."""
    if CTX is not None:
        return CTX
    env = os.environ.get("CONTEXT_ROOT", "").strip()
    if env:
        return Path(env)
    if KIT.resolve() == kit_profile.KIT.resolve():
        return kit_profile.context_root()
    return KIT.parent / ".context"


def root() -> Path:
    """The workspace root: the `ROOT` override, else the directory that holds `.context/`."""
    return ROOT if ROOT is not None else ctx().parent

ENV = kit_profile.ENV_DIR

OK, WARN, ERR = "OK", "WARN", "ERR"



class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.counts = {OK: 0, WARN: 0, ERR: 0}
        self.findings: list[tuple[str, str, str]] = []  # (level, section, text)
        self.leak_hits = 0  # un-accepted leak hits block the stamp like an error would

    def h(self, title: str) -> None:
        self.lines.append(f"\n## {title}\n")

    def add(self, level: str, section: str, text: str) -> None:
        self.counts[level] += 1
        mark = {OK: "✅", WARN: "⚠️", ERR: "❌"}[level]
        self.lines.append(f"- {mark} {text}")
        if level != OK:
            self.findings.append((level, section, text))

    def raw(self, text: str) -> None:
        self.lines.append(text)


def sh(cmd: list[str] | str, cwd: Path | None = None, env: dict | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """(exit status, stdout, stderr), both stripped — apart, so a summary line on stdout is never displaced by a
    warning on stderr. A command that cannot run is (127, "", <why>)."""
    e = dict(os.environ)
    e.setdefault("CONTEXT_ROOT", str(ctx()))  # children resolve the workspace as this run did, whatever their cwd (#3)
    if env:
        e.update(env)
    try:
        p = subprocess.run(cmd, cwd=cwd or root(), env=e, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 127, "", str(ex)


def both(out: str, err: str) -> str:
    """stdout and stderr joined for a failure body."""
    return "\n".join(x for x in (out, err) if x)


def frontmatter(p: Path) -> dict[str, str]:
    """Flat `key: value` frontmatter (the HEALTH stamp) — the shared parser, values unquoted."""
    fm = fmt.load(p) or {}
    return {k: fmt.unquote(v) for k, v in fm.items() if isinstance(v, str)}


def settings_local_path() -> Path:
    """Where this install keeps the ignored settings.local.json: beside the kit on a clone; on a plugin install (the kit in
    Claude Code's plugin cache) setup.sh seeds it in `<workspace>/.claude/`, so look there when the kit has none."""
    beside = KIT / "settings.local.json"
    if beside.is_file() or KIT.name == ".claude":
        return beside
    return ctx().parent / ".claude" / "settings.local.json"


def settings_local_env() -> dict:
    """The `env` block of settings.local.json (`{}` when absent or invalid). Callers match, never print."""
    p = settings_local_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("env", {}) or {}
    except json.JSONDecodeError:
        return {}


def identity_env() -> dict[str, str]:
    """The user's identity as `{WORKSPACE_*: value}` from every source: the plugin option
    (`CLAUDE_PLUGIN_OPTION_<KEY>` — visible when kit-health runs inside a hook) wins, then the `WORKSPACE_*` variable in
    the environment (on a plugin install the SessionStart hook re-exported the `/plugin configure ai-baton` values into Bash; on a clone
    Claude Code merged settings.local.json `env`), then settings.local.json read directly (a script run outside a
    session). Never printed — callers match against it or report which source a key came from."""
    file_env = settings_local_env()
    out: dict[str, str] = {}
    for var in kit_profile.IDENTITY_KEYS:
        v = kit_profile.identity(var, {**{k: str(x) for k, x in file_env.items() if isinstance(x, str)}, **os.environ})
        if v:
            out[var] = v
    return out


def identity_set(key: str) -> bool:
    """True when the identity variable is set through either source. Never returns the value."""
    return bool(identity_env().get(key))


def identity_sources() -> dict[str, str]:
    """`{WORKSPACE_*: "option" | "settings.local.json" | "environment" | ""}` — which source supplies each identity key,
    never the value. `environment`: the variable is in the process environment but not in settings.local.json — the
    plugin hook's re-export (kit-health never sees `CLAUDE_PLUGIN_OPTION_*` from Bash) or a shell export."""
    file_env = settings_local_env()
    out = {}
    for var in kit_profile.IDENTITY_KEYS:
        in_env = bool(os.environ.get(var, "").strip())
        in_file = bool(str(file_env.get(var) or "").strip())
        if os.environ.get(kit_profile.option_var(var), "").strip():
            out[var] = "option"
        elif in_file:
            out[var] = "settings.local.json"
        elif in_env:
            out[var] = "environment"
        else:
            out[var] = ""
    return out


def health_path(envname: str) -> Path:
    """Where this environment's stamp lives — local to the machine."""
    return ctx() / "kit-health" / f"HEALTH-{envname}.md"


def rel(p: Path) -> str:
    for base in (KIT, root()):
        try:
            return str(p.relative_to(base))
        except ValueError:
            continue
    return str(p)


# ── 1. kit ──────────────────────────────────────────────────────────────────────────────────
def sec_kit(r: Report, stale: int) -> None:
    r.h("1 · Kit — frontmatter, env store, git")
    rc, out, err = sh([sys.executable, str(BIN / "kit_verify.py"), "--stale", str(stale)])
    if rc == 0:
        r.add(OK, "kit", f"kit-verify: {out.splitlines()[-1] if out else 'OK'}")  # the summary line, never a stderr note
    else:
        r.add(ERR, "kit", "kit-verify failed:\n```\n" + both(out, err) + "\n```")
    for line in err.splitlines():
        if line.strip().startswith("~ stale:"):
            r.add(WARN, "kit", f"stale (reviewed >{stale}d ago): {line.split('~ stale:',1)[1].strip()} — re-read it and bump `reviewed`")
    head, plugin, mode = kit_head(), kit_profile.plugin_install(KIT), kit_profile.install_mode(KIT)
    if plugin is None:
        r.raw(f"- kit commit: `{head[:7] or 'unknown'}`")
    else:
        r.raw(f"- kit commit: `{head[:7] or 'unknown'}` — plugin install{' v' + plugin['version'] if plugin['version'] else ''}"
              f"{' from `' + plugin['repo'] + '`' if plugin['repo'] else ''} (no git checkout; the commit is the one Claude Code recorded)")
    install_mode_check(r, mode)
    rc, out, err = sh(["sh", str(KIT / "sync-check.sh")])
    out = both(out, err)  # sync-check warns on stderr
    if out.strip():
        for line in out.splitlines():
            r.add(WARN, "kit", line.replace("WARN kit sync: ", "sync: "))
    elif plugin is not None:  # sync-check skips the git half without a checkout: never claim "in step with origin" (#3)
        r.add(OK, "kit", "sync: plugin install — no checkout to sync (updates come from the marketplace, see the release "
              "line below); env store up to date")
    else:
        r.add(OK, "kit", "sync: in step with origin, tree clean")
    release_check(r, plugin, mode)
    if plugin is not None:
        cache_wiring(r, plugin)
    review_ratio(r)


def old_wiring() -> list[str]:
    """What a `.claude/` clone left in the workspace, which `mode` (not a clone) does not use."""
    left = ["the `.claude/` clone"] if (root() / ".claude" / ".git").exists() else []
    md, mk = root() / "CLAUDE.md", root() / "Makefile"
    if md.is_file() and re.search(r"^@\.claude/WORKSPACE\.md\s*$", md.read_text(encoding="utf-8", errors="replace"), re.M):
        left.append("`@.claude/WORKSPACE.md` in the root CLAUDE.md")
    if mk.is_file() and re.search(r"^include \.claude/workspace\.mk", mk.read_text(errors="replace"), re.M):
        left.append("`include .claude/workspace.mk` in the root Makefile")
    return left


def install_mode_check(r: Report, mode: str) -> None:
    """§ 1 (#34): the mode the kit runs in (`kit_profile.install_mode`) against `kit.install_mode`, which setup.sh
    records. Not recorded → WARN (re-run setup.sh); different → WARN naming the old mode's wiring still in the
    workspace; a dev checkout beside the workspace's `.claude/` clone is expected, not a switch."""
    recorded = kit_profile.recorded_install_mode()
    if not recorded:
        r.add(WARN, "kit", f"install mode: `{mode}` — not recorded in the env store (`kit.install_mode`); "
              "`sh $BATON/setup.sh` records it")
    elif kit_profile.mode_to_record(recorded, mode, root()) == recorded:
        r.add(OK, "kit", f"install mode: `{mode}` = recorded" if mode == recorded else
              f"install mode: `{mode}` — a development checkout beside the workspace's `.claude/` clone (recorded `{recorded}`, "
              "the installed kit)")
    else:
        left = old_wiring() if recorded == "clone" else []
        r.add(WARN, "kit", f"install mode: the kit runs as `{mode}`, but setup.sh recorded `{recorded}` — this machine "
              "switched install mode; " + (f"the old clone's wiring is still there ({', '.join(left)}) — remove what "
                                           f"`{mode}` does not use, then " if left else "")
              + "re-run `sh $BATON/setup.sh` (it records the mode and re-checks the wiring)")


def semver(tag: str) -> tuple[int, ...] | None:
    """`v0.3.0` / `0.3.0` → (0, 3, 0); None for anything else (a pre-release, a `+N` suffix, a non-version tag)."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def latest_release_plugin(repo: str) -> tuple[dict | None, str]:
    """({tag, published, url}, "") for the kit repo's latest GitHub release, or (None, why) when it cannot be read."""
    if not repo:
        return None, "plugin.json names no `repository`"
    if not shutil.which("gh"):
        return None, "`gh` not installed"
    rc, out, err = sh(["gh", "release", "view", "-R", repo, "--json", "tagName,publishedAt,url"], env=kit_profile.gh_env(), timeout=30)
    if rc != 0:
        return None, f"`gh release view -R {repo}` failed: {(err or out).splitlines()[-1] if (err or out) else f'exit {rc}'}"
    try:
        d = json.loads(out)
        return {"tag": d["tagName"], "published": str(d.get("publishedAt") or "")[:10], "url": str(d.get("url") or "")}, ""
    except (ValueError, KeyError, TypeError):
        return None, "`gh release view` returned no release"


def latest_release_clone() -> tuple[dict | None, str]:
    """({tag, published, url}, "") for the highest `vX.Y.Z` tag on the checkout's origin, or (None, why). Asks origin
    with `git ls-remote` — read-only, no local ref moves (kit-health writes nothing without `--stamp`)."""
    rc, out, err = sh(["git", "-C", str(KIT), "ls-remote", "--tags", "--refs", "origin", "v*"], timeout=30)
    if rc != 0:
        return None, f"`git ls-remote origin` failed: {(err or out).splitlines()[-1] if (err or out) else f'exit {rc}'}"
    tags = [ln.rsplit("refs/tags/", 1)[-1] for ln in out.splitlines() if "refs/tags/" in ln]
    tags = [t for t in tags if semver(t)]
    if not tags:
        return None, "origin has no `vX.Y.Z` tag"
    return {"tag": max(tags, key=semver), "published": "", "url": ""}, ""


def installed_release_clone() -> str:
    """The release tag the checkout's HEAD descends from (`v0.2.2` also for `v0.2.2+3`), "" before the first tag."""
    rc, out, _ = sh(["git", "-C", str(KIT), "describe", "--tags", "--match", "v[0-9]*", "--abbrev=0"])
    return out if rc == 0 else ""


def plugin_names() -> tuple[str, str]:
    """(plugin, marketplace) as the kit's own manifests name them — the `claude plugin update` arguments."""
    names = []
    for f, default in (("plugin.json", "ai-baton"), ("marketplace.json", "ai-baton-kit")):
        try:
            names.append(str(json.loads((KIT / ".claude-plugin" / f).read_text(encoding="utf-8")).get("name") or default))
        except (OSError, ValueError, AttributeError):
            names.append(default)
    return names[0], names[1]


def release_check(r: Report, plugin: dict | None, mode: str) -> None:
    """§ 1: is a newer kit release out than the one installed (#33)? Newer → WARN with the update command for this
    install mode (`kit_profile.MODES`, #34); unreadable → an informational `latest release unknown` line, never a ✅
    (a failed lookup is not "up to date"); otherwise ✅. A plugin install asks GitHub, a checkout (clone or dev
    checkout) asks its origin's tags."""
    if plugin is not None:
        installed = f"v{plugin['version']}" if plugin.get("version") else ""
        latest, why = latest_release_plugin(plugin.get("repo", ""))
    else:
        installed = installed_release_clone()
        latest, why = latest_release_clone()
    name, market = plugin_names()
    how = kit_profile.mode_hint("update", mode, KIT, plugin=name, market=market) + ", re-run /kit-health"
    if latest is None:
        r.raw(f"- ❔ release: latest release unknown — {why}; installed {installed or 'unknown'}")
        return
    have, want = semver(installed), semver(latest["tag"])
    if have is None or want is None:  # a pre-release or non-version tag on either side: say so, never crash
        r.raw(f"- ❔ release: latest is {latest['tag']}, installed {installed or 'unknown'} — cannot compare")
    elif want > have:
        when = f", published {latest['published']}" if latest["published"] else ""
        notes = f" — release notes: {latest['url']}" if latest["url"] else ""
        r.add(WARN, "kit", f"release: {latest['tag']} available (installed {installed}{when}) — run {how}{notes}")
    else:
        r.add(OK, "kit", f"release: {installed} = latest release" if want == have
              else f"release: {installed} is ahead of the latest release {latest['tag']}")


def cache_runtime(parts: tuple[str, ...]) -> bool:
    """The plugin cache's own runtime files, never a hand edit: bytecode anywhere, Claude Code's top-level `.in_use/`
    markers, and what `.gitignore` names at its own depth — the top-level `sign-queue/` queue (not the
    `skills/sign-queue/` skill) and `evals/…/results/`."""
    return ("__pycache__" in parts or parts[-1].endswith(".pyc") or parts[0] in (".in_use", "sign-queue")
            or (parts[0] == "evals" and "results" in parts[1:3]))


def cache_edits(kit: Path, updated: str, grace: int = 120) -> list[str] | None:
    """Kit files in a plugin cache modified after Claude Code wrote the install (`updated`, the registry's ISO
    `lastUpdated`) — a hand edit that `claude plugin update` would overwrite and no PR carries (#11). None when the
    install time is unknown; runtime files (`cache_runtime`) are skipped. `grace` seconds absorb the unpack."""
    try:
        since = dt.datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp() + grace
    except ValueError:
        return None
    out = []
    for p in sorted(kit.rglob("*")):
        parts = p.relative_to(kit).parts
        if p.is_file() and not cache_runtime(parts) and p.stat().st_mtime > since:
            out.append("/".join(parts))
    return out


def cache_wiring(r: Report, plugin: dict) -> None:
    edits = cache_edits(KIT, plugin.get("updated", ""))
    if edits:
        r.add(WARN, "kit", f"plugin cache: {len(edits)} kit file(s) changed after the install ("
              + ", ".join(f"`{e}`" for e in edits[:3]) + (", …" if len(edits) > 3 else "")
              + ") — `claude plugin update` overwrites them: make the change in a kit checkout and open a PR (skill step 4)")
    elif edits is not None:
        r.add(OK, "kit", "plugin cache: no kit file changed since the install")
    else:
        r.raw("- plugin cache: edit check skipped — `installed_plugins.json` records no install time for this path")


# ── review findings ────────────────────────────────
# The CI reviewer posts every finding in one shape, `[STOP|WARN|NIT] path:line — claim (rule)`, so the kit can count what
# happened to them: a thread resolved before the merge = acted on (fixed, or answered and deferred); still open on a
# merged PR = dismissed. Read from the kit's own repo through `gh` (GraphQL: thread resolution is not in REST); one OK
# line, or nothing when gh/the remote is unavailable — never a finding of its own.
FINDING = re.compile(r"^\s*\[(STOP|WARN|NIT)\]\s+(\S+?):(\d+)\s+—\s+(.*?)\s*(?:\(([^()]+)\))?\s*$", re.M)
REVIEW_QUERY = """query($owner:String!,$name:String!,$n:Int!){ repository(owner:$owner,name:$name){
  pullRequests(states:MERGED, last:$n, orderBy:{field:UPDATED_AT,direction:ASC}){ nodes{ number
    reviewThreads(first:100){ nodes{ isResolved comments(first:1){ nodes{ author{login} body } } } } } } } }"""


def finding_stats(prs: list[dict], bot: str = "claude") -> dict:
    """Counts over merged PRs' review threads whose first comment is a bot finding in the fixed shape: per level and per
    rule, acted-on (resolved) vs dismissed (open). Pure — the tests feed it the GraphQL shape."""
    stats = {"prs": len(prs), "findings": 0, "acted": 0, "dismissed": 0, "levels": {}, "rules": {}}
    for pr in prs:
        for th in (pr.get("reviewThreads") or {}).get("nodes") or []:
            first = ((th.get("comments") or {}).get("nodes") or [{}])[0]
            if ((first.get("author") or {}).get("login") or "") != bot:
                continue
            m = FINDING.search(first.get("body") or "")
            if not m:
                continue
            level, rule = m.group(1), (m.group(5) or "unnamed rule").strip()
            key = "acted" if th.get("isResolved") else "dismissed"
            stats["findings"] += 1
            stats[key] += 1
            stats["levels"].setdefault(level, {"acted": 0, "dismissed": 0})[key] += 1
            stats["rules"].setdefault(rule, {"acted": 0, "dismissed": 0})[key] += 1
    return stats


def review_ratio(r: Report, n: int = 10) -> None:
    if not shutil.which("gh"):
        return
    rc, remote, _ = sh(["git", "-C", str(KIT), "remote", "get-url", "origin"], timeout=10)
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", remote.strip()) if rc == 0 else None
    if not m:
        return
    rc, out, _ = sh(["gh", "api", "graphql", "-f", f"query={REVIEW_QUERY}", "-F", f"owner={m.group(1)}", "-F", f"name={m.group(2)}",
                     "-F", f"n={n}"], env=kit_profile.gh_env(), timeout=30)
    if rc != 0:
        return  # offline, no token, not a GitHub remote: nothing to say, nothing to flag
    try:
        prs = json.loads(out)["data"]["repository"]["pullRequests"]["nodes"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return
    st = finding_stats(prs)
    if not st["findings"]:
        r.add(OK, "kit", f"review findings: none in the fixed shape on the last {st['prs']} merged PRs (docs/REVIEW.md § 4)")
        return
    per_level = ", ".join(f"{lv} {v['acted']}/{v['acted'] + v['dismissed']}" for lv, v in sorted(st["levels"].items()))
    worst = sorted(st["rules"].items(), key=lambda kv: -kv[1]["dismissed"])[:3]
    rules = "; ".join(f"`{k}` {v['acted']}/{v['acted'] + v['dismissed']}" for k, v in worst if v["dismissed"])
    r.add(OK, "kit", f"review findings acted on: {st['acted']}/{st['findings']} over the last {st['prs']} merged PRs ({per_level})"
          + (f" — most dismissed: {rules}" if rules else ""))


# ── 2. leaks ────────────────────────────────────────────────────────────────────────────────
# Generic SHAPES of environment facts — `context-db/bin/leak_shapes.py`, shared with `kit_verify --no-env`. No
# organisation's own values live there or here — those are read at run time from the env store (`kb.py` tables +
# config.json), so the scanner finds exactly what THIS environment knows to be a fact, wherever it sits in the kit.
LEAK_SHAPES = leak_shapes.LEAK_SHAPES
# only the frontmatter declarations are exempt per line (leak_shapes owns the rule); the scanner's own files are
# skipped per file (SKIP_FILE) — a line that merely mentions `kit-health` is scanned like any other
SKIP_LINE = leak_shapes.SKIP_LINE
SKIP_FILE = set(leak_shapes.SKIP_FILES)  # the scanners' own files, per file (leak_shapes.py owns the set)
GENERIC = {"true", "false", "none", "jira", "github", "slack", "notion", "datalake", "airflow", "dbt",
           "issues", "main", "master"}
# bare repo names that half of GitHub has and the kit uses as ordinary words: `setup.sh --personal` fills
# `tracker.repos` from `gh repo list` on a fresh workspace, and a `config` repo flagged every kit file (#118).
# The full `owner/<repo>` slug and the `<org>/<repo>` path shape still catch these repos.
COMMON_REPO_NAMES = {"config", "configs", "dotfiles", "docs", "notes", "scripts", "tools", "utils", "setup",
                     "infra", "test", "tests", "templates", "examples", "sandbox", "playground", "website",
                     "blog", "archive", "backup", "workspace", "projects"}
IDENTITY = "identity"  # `what` prefix of patterns whose match is never printed (settings.local.json values)


def scan_files() -> list[Path]:
    files = [KIT / "WORKSPACE.md", KIT / "README.md", KIT / "setup.sh", KIT / "sync.sh", KIT / "sync-check.sh",
             KIT / "hooks" / "pre-push", KIT / "hooks" / "commit-msg",
             KIT / "workspace.mk", KIT / "settings.json", KIT / "settings.local.example.json", KIT / "CLAUDE.example.md",
             KIT / "CONTRIBUTING.md"]  # not the root CHANGELOG.md: the generated release log is history
    for sub in ("context-db", "agents", "environment-template", "docs", "skills", ".github"):
        files += [p for p in (KIT / sub).rglob("*") if p.is_file()]
    # leak_shapes.skip_path is the one rule; `fixtures/` files stay in the list so section 2 can count them
    return sorted({f for f in files if f.is_file() and "__pycache__" not in f.parts
                   and ("fixtures" in f.relative_to(KIT).parts or not leak_shapes.skip_path(str(f.relative_to(KIT))))})


def kit_repo() -> str:
    """`owner/repo` of the kit itself (its own name is not a leak), "" when unknown: the checkout's origin, else
    plugin.json `repository` on a plugin install (#3 — without it the kit's own install lines read as leaks)."""
    rc, out, _ = sh(["git", "-C", str(KIT), "remote", "get-url", "origin"])
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", out.strip()) if rc == 0 else None
    if m:
        return m.group(1)
    plugin = kit_profile.plugin_install(KIT)
    return plugin["repo"] if plugin else ""


def kit_head() -> str:
    """The kit's full commit: git HEAD, else the commit Claude Code recorded for a plugin install, else ""."""
    rc, head, _ = sh(["git", "-C", str(KIT), "rev-parse", "HEAD"])
    if rc == 0 and head:
        return head
    plugin = kit_profile.plugin_install(KIT)
    return plugin["commit"] if plugin else ""


def kit_owner_handle(frel: str, line: str, m: re.Match, owner: str) -> bool:
    """True for an `@<owner>` entry in `.github/CODEOWNERS` naming the kit repo's own owner (#5): CODEOWNERS must name a
    person, so the maintainer's handle there is the kit's own configuration, not a machine value. Any other login in
    CODEOWNERS, and the owner's handle anywhere else, still counts."""
    return (frel == ".github/CODEOWNERS" and bool(owner) and m.group(0).lower() == owner.lower()
            and m.start() > 0 and line[m.start() - 1] == "@")


@functools.lru_cache(maxsize=None)
def kit_dependencies() -> frozenset[str]:
    """Names of the tools the kit itself installs or calls (`uvx --from '<pkg>`, `uses: <owner>/<repo>`, the ctx
    adapter's pinned `CTX_REPO`) plus the kit's own repo name — naming a dependency is not a leak, even when a
    tracked repo shares the name."""
    deps = {kit_repo().rsplit("/", 1)[-1]} - {""}
    try:
        adapter = (KIT / "context-db" / "bin" / "ctx_adapter.py").read_text(encoding="utf-8")
    except OSError:
        adapter = ""
    deps |= set(re.findall(r'^CTX_REPO\s*=\s*"[^"]*/([\w.-]+?)(?:\.git)?"', adapter, re.M))
    for f in [KIT / "workspace.mk", *sorted((KIT / ".github" / "workflows").glob("*.yml"))]:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        deps |= set(re.findall(r"--from\s+'?([A-Za-z0-9_.-]+)", text))
        deps |= {m.rsplit("/", 1)[-1] for m in re.findall(r"uses:\s*([\w.-]+/[\w.-]+)", text)}
    return frozenset(deps)


def identity_values() -> list[tuple[re.Pattern, str]]:
    """The user's identity — full name, `first.last`, the GitHub login — from the plugin options and
    settings.local.json env (the option values are scanned too, so a login typed into `/plugin configure ai-baton` that
    lands in a kit file is a finding). Not the single name parts: many are ordinary words (Will, Page, Mark)
    and would flood the report. Case-insensitive; the caller never prints what these match, so the label
    names which form hit."""
    env = identity_env()
    forms: dict[str, str] = {}
    user = str(env.get("WORKSPACE_USER") or "").strip()
    if user:
        parts = [x for x in re.split(r"\s+", user) if x]
        forms[user] = "WORKSPACE_USER, full name"
        if len(parts) > 1:
            forms[".".join(parts)] = "WORKSPACE_USER, first.last"
    login = str(env.get("WORKSPACE_GITHUB_LOGIN") or "").strip()
    if len(login) >= 3:
        forms[login] = "WORKSPACE_GITHUB_LOGIN"
    return [(re.compile(rf"(?<![\w-]){re.escape(v)}{address_tail()}", re.I), f"{IDENTITY} ({k}, identity value)")
            for v, k in forms.items()]


def address_tail() -> str:
    """Regex tail for a login: a whole word, and not the owner part of the kit's own repo or a dependency
    (`<owner>/<dep>` is the kit's address, not a leak)."""
    deps = "|".join(re.escape(d) for d in sorted(kit_dependencies()))
    return rf"(?![\w-])(?!/(?:{deps})\b)" if deps else r"(?![\w-])"


def common_word_kinds() -> set[str]:
    """`<system>.<kind>` of every manifest fact flagged `common_word: true` — kinds whose values are ordinary words
    (a person's first name, a team, a label) and would flood the value scan. Read from the manifests, so an
    environment's own `_discovery/` overlay can add one."""
    out: set[str] = set()
    try:
        for m in kb.load_manifests().values():
            for f in m.get("facts") or []:
                if isinstance(f, dict) and f.get("common_word") and f.get("target", "row") == "row":
                    out.add(str(f.get("key", "")).split(" ")[0])
    except (OSError, ValueError, TypeError):
        pass
    return out


def keep_value(v: str, kind: str = "", common: set[str] | frozenset[str] = frozenset()) -> bool:
    """Is this configured value worth scanning for? Not when it is short, generic or a placeholder — those match
    everywhere and mean nothing. For an env-store ROW (`kind` given): a common-word kind (`common_word: true` in its
    manifest — first names, teams, labels) skips a plain single word but still scans a value with a space or hyphen
    (`First Last`, `<team>-platform` are the leaks the scan exists for), and any kind skips a plain word under six
    letters. A config or identity value (no `kind`) keeps the old four-character floor: a short org or
    login is the leak most worth catching."""
    s = v.strip()
    if len(s) < 4 or s.lower() in GENERIC or (s.isdigit() and len(s) < 6) or s.startswith("<"):
        return False
    if not kind:
        return True
    plain_word = re.fullmatch(r"[A-Za-z]+", s) is not None
    if plain_word and kind in common:
        return False
    if plain_word and len(s) < 6:
        return False
    return True


def redact(what: str, value: str) -> str:
    """`<kind>:<first2>…` — the report and the audit log name the kind and two characters, never the value.
    Shapes give their name as `what`; configured values their key."""
    kind = what.split(" (")[0].replace("env fact ", "").strip("`")
    return f"{kind}:{value[:2]}…" if len(value) > 2 else f"{kind}:…"


def changed_units(commit: str) -> list[str] | None:
    """Skill/agent dirs and WORKSPACE.md changed since `commit` — what the judgement pass re-reads. None when git
    cannot answer (unknown commit, no checkout): the caller says so instead of "nothing changed"."""
    rc, out, _ = sh(["git", "-C", str(KIT), "diff", "--name-only", f"{commit}..HEAD", "--", "skills", "agents", "WORKSPACE.md"], timeout=30)
    if rc != 0:
        return None
    return group_units(out.split())


def group_units(paths: list[str]) -> list[str]:
    """Changed paths → the units the judgement pass re-reads (`skills/<name>`, `agents/<file>`, `WORKSPACE.md`), in
    first-seen order; every other path is dropped."""
    units: list[str] = []
    for path in paths:
        parts = path.split("/")
        if parts[0] == "skills" and len(parts) > 1:
            u = f"skills/{parts[1]}"
        elif parts[0] == "agents" or path == "WORKSPACE.md":
            u = path
        else:
            continue
        if u not in units:
            units.append(u)
    return units


def changed_units_remote(repo: str, base: str, head: str) -> list[str] | None:
    """The same list on a plugin install, which has no git history: GitHub's compare API between the stamp's commit
    and the installed one (#13). None when it cannot answer (no repo/commit, no `gh`, offline, unknown commit)."""
    if not (repo and base and head and shutil.which("gh")):
        return None
    rc, out, _ = sh(["gh", "api", f"repos/{repo}/compare/{base}...{head}", "--jq", ".files[].filename"],
                    env=kit_profile.gh_env(), timeout=30)
    return group_units(out.split()) if rc == 0 else None


def may_stamp(r: "Report") -> bool:
    """The stamp means "the kit works everywhere": no errors and no un-accepted leak hit."""
    return not r.counts[ERR] and not r.leak_hits


def configured_values() -> tuple[list[tuple[re.Pattern, str]], list[str]]:
    """(patterns, loader errors). Every literal value this environment has configured: env-store table values +
    config keys that carry identity (org, review bot, hosts, channel ids, colleague logins, tracked repos, domains)
    + the user's own identity. Short / numeric / generic values are skipped — they would match everywhere. A store
    that cannot be read is an ERROR the caller reports, never a silent shapes-only scan."""
    vals: dict[str, str] = {}
    logins: list[tuple[re.Pattern, str]] = []
    errors: list[str] = []

    common = common_word_kinds()

    def keep(v: object, what: str, kind: str = "") -> None:
        if keep_value(str(v), kind, common):
            vals.setdefault(str(v).strip(), what)

    try:
        for system, kinds in kb.all_facts().items():
            for kind, rows in kinds.items():
                for row in rows.values():
                    keep(row["value"], f"env fact `{system}.{kind}`", f"{system}.{kind}")
    except (OSError, ValueError, KeyError, SystemExit) as e:
        errors.append(f"env-store tables could not be read ({e}) — the value scan is shapes-only until that is fixed")
    try:
        cfg = kit_profile.load()
    except SystemExit as e:
        errors.append(f"env-store config could not be loaded ({e}) — configured values not scanned")
        cfg = {}
    if cfg:
        gh = (cfg.get("github") or {})
        keep(gh.get("review_bot"), "github.review_bot")
        for login in (gh.get("display_names") or {}):
            if keep_value(str(login)):  # the kit's own `<owner>/<repo>` address is not a leak (#118)
                logins.append((re.compile(rf"(?<![\w-]){re.escape(str(login).strip())}{address_tail()}"),
                               "colleague login (github.display_names)"))
        for team in gh.get("owner_teams") or []:
            keep(team, "github.owner_teams")
        keep((cfg.get("slack") or {}).get("domain"), "slack.domain")
        for v in ((cfg.get("slack") or {}).get("channels") or {}).values():
            keep(v, "Slack channel id (slack.channels)")
        for k in ("site", "project", "board_sprint_prefix"):
            keep((cfg.get("tracker") or {}).get(k), f"tracker.{k}")
        keep(cfg.get("tz_default"), "tz_default")
        for m in (cfg.get("leaks") or {}).get("markers") or []:  # #95: product/org markers no shape knows
            keep(m, "leaks.markers")
        deps = kit_dependencies()
        for repo in (cfg.get("tracker") or {}).get("repos") or []:
            if str(repo).rsplit("/", 1)[-1] in deps:
                continue
            keep(repo, "tracker.repos")
            if str(repo).rsplit("/", 1)[-1].lower() not in COMMON_REPO_NAMES:
                keep(str(repo).rsplit("/", 1)[-1], "tracker.repos (repo name)")
    pats: list[tuple[re.Pattern, str]] = []
    # a domain name is often an ordinary word — flag only its path-like uses; a domain the engine itself
    # owns (verify.py CORE_DOMAINS — e.g. `on-call`, where `new.sh TYPE=oncall` writes) is a kit constant,
    # not an environment value, even when the env config lists it under `domains`
    try:
        from verify import CORE_DOMAINS as _core  # same bin/ dir as kit_profile
    except (ImportError, SystemExit) as e:
        _core = set()
        errors.append(f"verify.py could not be imported for CORE_DOMAINS ({e}) — engine domains scanned as environment values")
    for d in sorted({str(d).strip() for d in (cfg.get("domains") or [])} - {""} - set(_core)):
        pats.append((re.compile(rf"(?:\.context/|DOMAIN=){re.escape(d)}\b"), "domains (a local .context/ domain)"))
    for v, what in vals.items():
        esc = re.escape(v)
        pats.append((re.compile((r"\b" if v[0].isalnum() else "") + esc + (r"\b" if v[-1].isalnum() else "")), what))
    orgs = {str((cfg.get("github") or {}).get("org") or "")} - {""}
    for org in sorted(orgs):
        deps = "|".join(re.escape(d) for d in sorted(kit_dependencies()))
        not_dep = rf"(?!(?:{deps})\b)" if deps else ""
        pats.append((re.compile(rf"\b{re.escape(org)}/{not_dep}[a-z][\w.-]*"), f"`{org}/<repo>` path"))
        pats.append((re.compile(rf"@{re.escape(org)}/"), "org team handle"))
    return pats + logins + identity_values(), errors


def sec_leaks(r: Report) -> None:
    r.h("2 · Leaks — environment-specific values anywhere in the kit")
    try:
        tracker = kit_profile.get("tracker") or {}
    except SystemExit as e:  # an unreadable config.json is section 3's ERR; the scan runs on the generic shapes
        r.add(ERR, "leaks", f"env-store config could not be loaded ({e}) — ticket shape is the generic one")
        tracker = {}
    shapes = leak_shapes.shapes(tracker.get("kind") if isinstance(tracker, dict) else None,
                                tracker.get("key_regex") if isinstance(tracker, dict) else None)
    values, load_errors = configured_values()
    for e in load_errors:
        r.add(ERR, "leaks", e)
    pats_all = shapes + values
    fixtures_skipped = 0
    allowed = leak_shapes.allowed()  # skills/kit-health/allow.txt — the same list kit-verify's unit scan reads
    repo = kit_repo()
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    files = scan_files()
    hits = 0
    files_hit = 0
    for f in files:
        frel = str(f.relative_to(KIT))
        if leak_shapes.skip_path(frel):  # the review gate's rule too: fixtures/ dirs are test data
            fixtures_skipped += 1
            continue
        pats = pats_all
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        shown = 0
        more = 0
        for n, line in enumerate(text.split("\n"), 1):
            if SKIP_LINE.search(line):
                continue
            # every match on the line, each checked against the allow-list
            for what, hit in leak_shapes.line_hits(line, pats, frel, allowed,
                                                   keep=lambda m, line=line: not kit_owner_handle(frel, line, m, owner)):
                hits += 1
                if shown < 3:
                    shown += 1
                    shown_val = "(value withheld)" if what.startswith(IDENTITY) else f"`{redact(what, hit)}`"
                    r.add(WARN, "leaks", f"`{frel}:{n}` {what} {shown_val} — move it to the env store (`kb.py set <system>.<kind> <name> <value>`, read back with `kb.py get`) or use a `<placeholder>`")
                else:
                    more += 1
        if shown:
            files_hit += 1
        if more:
            r.raw(f"  - `{frel}`: +{more} more hit(s) of the same kind (fix the file, re-run)")
    if fixtures_skipped:
        r.raw(f"  - {fixtures_skipped} fixture file(s) skipped (`fixtures/` dirs hold test data, not kit prose)")
    if not hits:
        r.add(OK, "leaks", f"no environment-specific values in {len(files)} kit files ({len(shapes)} shapes + {len(values)} configured values checked"
              + (" — value scan degraded, see the errors above" if load_errors else "") + ")")
    else:
        r.raw(f"- **{hits} hit(s) in {files_hit} file(s)** — {len(pats_all)} shapes+values checked across {len(files)} files; "
              "`--stamp` refuses while any hit is un-accepted (fix it, or allow.txt with a reason)")
    r.leak_hits = hits


# ── 3. config ───────────────────────────────────────────────────────────────────────────────
def sec_config(r: Report) -> None:
    r.h("3 · Config — env fact store")
    if (ENV / "config.json").is_file():
        facts = kb.all_facts()
        n = sum(len(rows) for kinds in facts.values() for rows in kinds.values())
        per = ", ".join(f"{s} {sum(len(x) for x in kinds.values())}" for s, kinds in facts.items()) or "no tables yet"
        r.add(OK, "config", f"env store `{rel(ENV)}`: config.json + {n} facts ({per})")
        try:
            cfg = kb.load_config()
            # a key is only wanted where something reads it — github.org by a GitHub-tracked or bot-reviewed
            # store, tz_default only when the identity carries no zone
            wanted = ["environment", "tracker.kind", "systems"]
            if kb.dotted_get(cfg, "tracker.kind") == "github" or kb.dotted_get(cfg, "github.review_bot"):
                wanted.append("github.org")
            if not kit_profile.identity("WORKSPACE_TZ"):
                wanted.append("tz_default")
            for k in wanted:
                if kb.dotted_get(cfg, k) in (None, "", {}):
                    r.add(WARN, "config", f"env store config.json: `{k}` empty — `kb.py config-set {k} <value>`")
        except json.JSONDecodeError:
            r.add(ERR, "config", "env store config.json is not valid JSON")
        # rows past their manifest ttl: the monthly refresh is `/env-init --refresh` (env-facts.md § How a skill resolves a fact)
        rc, out, err = sh([sys.executable, str(BIN / "kb.py"), "stale", "--check"])
        out = both(out, err)
        if rc == 0:
            r.add(OK, "config", "`kb.py stale`: no tool-sourced row past its ttl")
        elif rc == 3:
            rows_ = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.lower().startswith("kb:")]
            r.add(WARN, "config", f"`kb.py stale`: {len(rows_)} row(s) past their ttl — run `/env-init --refresh` (re-verifies each via its "
                                  "discovery tool; never re-date by hand):\n```\n" + "\n".join(rows_[:20]) + ("\n…" if len(rows_) > 20 else "") + "\n```")
        else:
            r.add(ERR, "config", f"`kb.py stale --check` failed (exit {rc}): {out[-300:]}")
    else:
        r.add(ERR, "config", "env store missing — `python3 $BATON/context-db/bin/kb.py init --blank`, "
                             "then fill config.json (`kb.py config-set`) and facts (`kb.py set`)")
        return
    systems_active = kit_profile.get("systems") or {}
    # One source with `kb.py migrate --off`: skills *and* agents, `<unit> (<flag>, …)` per not-applicable unit.
    na = kb.unmet_units({"systems": systems_active})
    r.raw(f"  - skills/agents not applicable in this environment (a `requires:` flag is false): {', '.join(na) or 'none'}")
    renamed = [o for o in kb.RENAMED_SYSTEMS if o in systems_active]
    if renamed:
        r.add(WARN, "config", f"renamed capability flag(s) still in config.json: {', '.join('systems.' + o for o in renamed)} — "
                              "run `python3 $BATON/context-db/bin/kb.py migrate` (keeps the value)")
    # one flag per capability: `slack.enabled` / `github.signed_commits` are retired — every reader now
    # gates on `systems.slack` / `systems.signed_commits` (`kit_profile.DEPRECATED_ALIASES`). A store that still
    # carries the old key is not a kit-verify failure (an extra key kit-verify never checked for), so this is
    # the one place a machine hears about it — loudly when the two disagree, since that is exactly the split
    # read this refactor closes.
    _absent = object()
    for old, new in kit_profile.DEPRECATED_ALIASES.items():
        old_val = kit_profile.get(old, _absent)
        if old_val is _absent:
            continue
        new_val = bool(kit_profile.get(new))
        old_val = bool(old_val)
        disagree = (f" — THEY DISAGREE: `{old}` = {str(old_val).lower()}, `{new}` = {str(new_val).lower()}, "
                    f"and `{new}` is what's read" if old_val != new_val else "")
        r.add(WARN, "config", f"config.json still carries the deprecated `{old}`; `{new}` is what every skill/script "
                              f"reads now{disagree} — no `kb.py` command removes a config key yet: delete `{old}` "
                              "from config.json by hand")


# ── 4. machine ──────────────────────────────────────────────────────────────────────────────
def identity_wiring(r: Report) -> None:
    """§ 4's identity rows: which source supplies each key, one ERR only when no source supplies any."""
    src = identity_sources()
    settings = settings_local_path()
    hooked = [k for k, v in src.items() if v in ("option", "environment")]
    if hooked:  # the plugin path: /plugin configure ai-baton options re-exported by the SessionStart hook (or a shell export) — the file is optional
        r.add(OK, "machine", "identity from the environment (`/plugin configure ai-baton` via the SessionStart hook, or a shell export): "
              + ", ".join(f"`{k}`" for k in hooked))
        if not settings.is_file():
            r.add(OK, "machine", "`settings.local.json` absent — not needed, the environment carries the identity")
    elif not settings.is_file():
        r.add(ERR, "machine", "no identity from any source: `settings.local.json` missing (looked at `"
              + (str(settings.relative_to(root())) if settings.is_relative_to(root()) else str(settings))
              + "`) and no `WORKSPACE_*` in the environment — run `sh $BATON/setup.sh`, or `/plugin configure ai-baton` on a plugin install "
              "(values set there reach Bash from the next session on: restart it if you just configured them)")
    for k in ("WORKSPACE_USER", "WORKSPACE_GITHUB_LOGIN", "WORKSPACE_TZ"):
        if not src[k]:
            r.add(WARN, "machine", f"`{k}` unset — `/plugin configure ai-baton` (plugin) or settings.local.json env (clone); scripts fall back to `gh api user` / UTC where they can")
    zone_warning(r, src)


def zone_warning(r: Report, src: dict[str, str]) -> None:
    """An unknown display zone is a WARN, not a silent UTC fallback behind a GREEN verdict (#22): every timestamp and the
    log date would render in UTC. Names where the value comes from, never the value."""
    name = kit_profile.tz()
    try:
        kit_profile.ZoneInfo(name)
        return
    except Exception:  # ZoneInfoNotFoundError, ValueError, OSError on a directory-shaped name
        pass
    source = src.get("WORKSPACE_TZ", "")
    if source == "environment" and kit_profile.plugin_install(KIT) is not None:
        # Bash sees the hook's re-export of the /plugin configure ai-baton option and a shell export alike — name both (#30)
        source = "hook-or-shell"
    where = {"option": "the plugin option `tz` (`/plugin configure ai-baton`)",
             "hook-or-shell": "the plugin option `tz` (`/plugin configure ai-baton`, which the SessionStart hook exports as "
                              "`WORKSPACE_TZ`) or a shell export of `WORKSPACE_TZ`",
             "settings.local.json": "`WORKSPACE_TZ` in settings.local.json",
             "environment": "`WORKSPACE_TZ` in the environment"}.get(source, "the env store's `tz_default`")
    r.add(WARN, "machine", f"the display zone from {where} is not an IANA zone — timestamps and the log date render in UTC; "
          "set an IANA Region/City name (an abbreviation like `EST` or `EDT` is not one)")


def seed_pairs() -> list[tuple[Path, Path]]:
    """(template in the kit, seeded copy on this machine) — what setup.sh seeds once and never overwrites."""
    return [(KIT / "context-db" / "context-README.template.md", ctx() / "README.md"),
            (KIT / "environment-template" / "environment.md", ctx() / "reference" / "environment.md"),
            (KIT / "CLAUDE.example.md", root() / "CLAUDE.md"),
            (KIT / "context-db" / "_templates" / "self-assessment-charter.md", ctx() / "self-assessment" / "README.md")]


def stale_seeds(pairs: list[tuple[Path, Path]] | None = None, template_time=None) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]:
    """(stale, unknown): `stale` = pairs whose seeded copy is older than the template's last commit (a template fix
    that never reached this machine); `unknown` = pairs that exist but could not be dated (no git history for the
    template — a plugin install, a copy without `.git`), which is reported as not compared, never as fresh.
    `template_time(path) -> int epoch` defaults to `git log -1 --format=%ct`, 0 when git cannot answer."""
    def git_time(p: Path) -> int:
        rc, out, _ = sh(["git", "-C", str(KIT), "log", "-1", "--format=%ct", "--", str(p)], timeout=30)
        return int(out.strip()) if rc == 0 and out.strip().isdigit() else 0
    ttime = template_time or git_time
    stale, unknown = [], []
    for tpl, copy in (pairs if pairs is not None else seed_pairs()):
        if not (tpl.is_file() and copy.is_file()):
            continue
        t = ttime(tpl)
        if not t:
            unknown.append((tpl, copy))
        elif t > int(copy.stat().st_mtime):
            stale.append((tpl, copy))
    return stale, unknown


def seed_wiring(r: Report) -> None:
    stale, unknown = stale_seeds()
    for tpl, copy in stale:
        r.add(WARN, "machine", f"seed `{rel(copy)}` predates its template `{rel(tpl)}` — `sh $BATON/setup.sh --refresh-seeds` shows the diff; merge what you want by hand")
    if unknown:
        r.raw("- seed freshness not checked for " + ", ".join(f"`{rel(c)}`" for _t, c in unknown)
              + " — the kit has no git history here (plugin install?); `sh $BATON/setup.sh --refresh-seeds` still prints the diffs")
    elif not stale:
        r.add(OK, "machine", "seeded files (.context/README.md, environment.md, CLAUDE.md, the self-assessment charter) are not older than their templates")


def pr_review_example_edited(example: Path | None = None) -> list[str]:
    """Keys in `pr-review/config.example.json` that no longer hold their placeholder shape (a leading `<`) —
    a sign the file itself was hand-edited instead of the seeded `.context/state/pr-review/config.json`
    (`docs/conventions.md`); editing the example changes nothing on this machine and shows up as a kit diff.
    `example` defaults to the kit's own copy; a test passes a throw-away one."""
    p = example if example is not None else KIT / "pr-review" / "config.example.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [k for k in ("login", "owner") if not str(cfg.get(k, "")).startswith("<")]


def sandbox_detected(markers: list) -> bool:
    """True when any `kit.sandbox_markers` entry holds (#94): an absolute path that exists, or an environment variable
    that is set. The kit knows no sandbox product; a machine that runs in one names its own markers in the env store,
    and without markers there is no sandbox check (the `gh` reachability line above still warns)."""
    for mk in markers:
        mk = str(mk).strip()
        if mk and (os.path.exists(mk) if mk.startswith("/") else bool(os.environ.get(mk))):
            return True
    return False


def sec_machine(r: Report) -> str:
    r.h("4 · This machine — wiring")
    envname = kit_profile.name()
    identity_wiring(r)
    seed_wiring(r)
    if not (ENV / "config.json").is_file():
        r.add(ERR, "machine", "no configuration at all — `python3 $BATON/context-db/bin/kb.py init --blank`")
    elif kit_profile.env_config().get("environment"):
        r.add(OK, "machine", f"active environment `{envname}` from the env store")
    else:
        r.add(WARN, "machine", f"the env store names no `environment` — defaulting to `{envname}`; `kb.py config-set environment <name>` fixes it")
    claude_md = root() / "CLAUDE.md"
    env_doc = ctx() / "reference" / "environment.md"
    if not claude_md.is_file():
        r.add(ERR, "machine", "root `CLAUDE.md` missing — `sh $BATON/setup.sh` seeds it")
    else:
        text = claude_md.read_text(encoding="utf-8", errors="replace")
        imp_ws = re.search(r"^@\.claude/WORKSPACE\.md\s*$", text, re.M)
        ws_file = (root() / ".claude" / "WORKSPACE.md").is_file()
        hook = kit_profile.mode_hint("workspace_md", kit_profile.install_mode(KIT)) == "hook"  # #34: from the mode table
        if imp_ws and not ws_file:
            # the import line alone is not wiring: a missing target loads nothing, silently (#3)
            r.add(ERR, "machine", "CLAUDE.md imports `@.claude/WORKSPACE.md` but the file is missing — "
                  + ("remove the line: the plugin's SessionStart hook injects WORKSPACE.md" if hook
                     else "the kit's always-on rules are NOT loaded"))
        elif imp_ws:
            r.add(OK, "machine", "CLAUDE.md imports `@.claude/WORKSPACE.md`")
        elif hook and not ws_file and (ctx() / "reference" / "env").is_dir():
            r.add(OK, "machine", "WORKSPACE.md comes from the plugin's SessionStart hook (`kit_profile.py workspace-rules`) — no import needed")
        else:
            r.add(ERR, "machine", "CLAUDE.md does not import `@.claude/WORKSPACE.md`")
        imp_env = re.search(r"^@\.context/reference/environment\.md\s*$", text, re.M)
        if env_doc.is_file() and imp_env:
            r.add(OK, "machine", "CLAUDE.md imports `@.context/reference/environment.md`")
        elif env_doc.is_file():
            r.add(WARN, "machine", "`.context/reference/environment.md` exists but CLAUDE.md does not import it — its rules are not loaded")
        elif imp_env:
            r.add(ERR, "machine", "CLAUDE.md imports `@.context/reference/environment.md` but the doc is missing — seed it from `$BATON/environment-template/environment.md`")
        else:
            r.add(WARN, "machine", "no `.context/reference/environment.md` — the environment's prose (repos, venues, capabilities) is not written down; seed it from `$BATON/environment-template/environment.md`")
    if LEGACY_PROFILES.exists():  # the one legacy line kept
        r.add(WARN, "machine", "legacy `.claude/profiles/` present → delete it (`rm -rf .claude/profiles`; the layer retired 2026-09-25, nothing reads it)")
    mk = root() / "Makefile"
    mk_inc = mk.is_file() and re.search(r"^include \.claude/workspace\.mk", mk.read_text(errors="replace"), re.M)
    mode = kit_profile.install_mode(KIT)
    uses_mk = kit_profile.mode_hint("makefile", mode)  # #34: only a clone includes workspace.mk
    if mk_inc and not (root() / ".claude" / "workspace.mk").is_file():
        r.add(ERR, "machine", "root Makefile does `include .claude/workspace.mk` but the file is missing — every `make` in the "
              "workspace fails" + ("" if uses_mk else "; remove the line (its targets drive a `.claude/` clone)"))
    elif mk_inc:
        r.add(OK, "machine", "root Makefile includes `.claude/workspace.mk`")
    elif not uses_mk:
        r.add(OK, "machine", f"no `workspace.mk` include — not used on a {'plugin install' if mode == 'plugin' else 'dev checkout'} "
              "(its targets drive a `.claude/` clone)")
    else:
        r.add(WARN, "machine", "root Makefile missing or without `include .claude/workspace.mk` (no `make claude_sync` / `sign*`)")
    if (ctx() / "README.md").is_file():
        r.add(OK, "machine", "`.context/README.md` present")
    else:
        r.add(WARN, "machine", "`.context/README.md` missing — `sh $BATON/setup.sh` seeds it")
    slug = kit_profile.harness_project_slug(str(root()))
    hm = Path.home() / ".claude" / "projects" / slug / "memory"
    if hm.is_symlink() and hm.resolve() == (ctx() / "memory").resolve():
        r.add(OK, "machine", "harness memory dir → `.context/memory` symlink")
    else:
        r.add(WARN, "machine", "harness memory dir is not a symlink to `.context/memory` — `sh $BATON/setup.sh`")
    prc = ctx() / "state" / "pr-review" / "config.json"
    org = kit_profile.get("github.org") or ""
    if prc.is_file():
        try:
            c = json.loads(prc.read_text(encoding="utf-8"))
            if not c.get("login") or c["login"].startswith("<"):
                r.add(WARN, "machine", "`.context/state/pr-review/config.json`: `login` not set")
            if c.get("owner") != org:
                r.add(WARN, "machine", f"`.context/state/pr-review/config.json`: `owner` is `{c.get('owner')}` but the configured `github.org` is `{org}`")
            bad = [x for x in c.get("sweep_repos", []) if org and not x.startswith(org + "/")]
            if bad:
                r.add(WARN, "machine", f"pr-review config `sweep_repos` outside `{org}/`: {', '.join(bad)}")
            if c.get("owner") == org and not bad and c.get("login") and not c["login"].startswith("<"):
                r.add(OK, "machine", f"pr-review config matches the configured org (`owner` = `{org}`)")
        except json.JSONDecodeError:
            r.add(ERR, "machine", "`.context/state/pr-review/config.json` is not valid JSON")
    else:
        r.add(WARN, "machine", "`.context/state/pr-review/config.json` missing — `sh $BATON/setup.sh` seeds it")
    edited = pr_review_example_edited()
    if edited:
        r.add(WARN, "machine", f"`pr-review/config.example.json` {', '.join(edited)} no longer look(s) like a "
              "placeholder — editing the example changes nothing on this machine; edit "
              "`.context/state/pr-review/config.json` instead (`docs/conventions.md`)")
    for tool in ("git", "make", "python3", "gh", "jq"):
        if shutil.which(tool):
            r.add(OK, "machine", f"CLI `{tool}` present")
        else:
            lvl = ERR if tool in ("git", "make", "python3") else WARN
            r.add(lvl, "machine", f"CLI `{tool}` missing" + (" — `pr-scan.sh` / `pr-watch.sh` need it" if tool in ("gh", "jq") else ""))
    if shutil.which("gh"):
        # exactly the way every kit script calls gh: a native login first, else github.sandbox_token_prefix when set
        pref = kit_profile.gh_token_prefix()
        rc, out, _ = sh(["gh", "api", "user", "--jq", ".login"], env=kit_profile.gh_env(), timeout=30)
        how = f"`{pref[0]}` placeholder from `github.sandbox_token_prefix`" if pref and not os.environ.get(pref[0]) else "native login"
        if rc == 0 and out and not out.startswith("gh:"):
            r.add(OK, "machine", f"GitHub API reachable via `gh` ({how})")
        else:
            r.add(WARN, "machine", f"GitHub API not reachable via `gh` ({how}) — offline, not logged in (`gh auth login`), "
                  "or a wrong `github.sandbox_token_prefix` (empty on a host whose gh is logged in; the placeholder "
                  "`NAME=value` in a sandbox whose proxy injects the token)")
        in_sandbox = sandbox_detected(kit_profile.get("kit.sandbox_markers") or [])
        if in_sandbox and not kit_profile.configured_token_prefix() and not os.environ.get("GH_TOKEN"):
            r.add(WARN, "machine", "sandbox detected but `github.sandbox_token_prefix` is empty — gh refuses to run without a "
                  "token there: `kb.py config-set github.sandbox_token_prefix <NAME=value>`")
    systems = kit_profile.get("systems") or {}
    enabled = [k for k, v in systems.items() if v]
    r.raw(f"- systems enabled in `{envname}`: {', '.join(enabled) or 'none'}")
    if systems.get("aws_sso"):
        if shutil.which("aws"):
            # profile: the env-store fact `aws.profile kit-health` when set, else bare — bare honours
            # AWS_PROFILE / [default]; a machine with only named SSO profiles sets the fact once
            prof = kb.get("aws", "profile", "kit-health")
            cmd = ["aws", "sts", "get-caller-identity"] + (["--profile", prof] if prof else [])
            rc, _, _ = sh(cmd, timeout=30)
            shown = f"`aws sts get-caller-identity{' --profile ' + prof if prof else ''}`"
            hint = "" if prof or rc == 0 else " (no profile — `/env-init aws.profile kit-health` on a machine with named SSO profiles)"
            r.add(OK if rc == 0 else WARN, "machine", "aws_sso: " + shown + (" OK" if rc == 0 else " failed — `aws-sso-login` skill before stage/prod checks" + hint))
        else:
            r.add(WARN, "machine", "aws_sso enabled but the `aws` CLI is missing")
    if systems.get("signed_commits"):
        # the queue lives in the workspace (#7); the kit's own `sign-queue/` is where a pre-#7 kit left jobs
        sq = ctx() / "state" / "sign-queue"
        pending = len(list(sq.glob("*.sh"))) if sq.is_dir() else 0
        r.add(OK, "machine", f"signed_commits: queue `.context/state/sign-queue/` " + (f"({pending} pending)" if sq.is_dir()
              else "not created yet (the first enqueue creates it)"))
        legacy = [p for p in (KIT / "sign-queue").glob("*.sh*")] if (KIT / "sign-queue").is_dir() else []
        if legacy:
            r.add(WARN, "machine", f"signed_commits: {len(legacy)} job(s) left in the kit's old `sign-queue/` — `make sign_list` moves them")
    if systems.get("lattice"):
        # Lattice has no connector (SSO web form): the self-assessment skill reads the Lattice bot DM
        # through Slack and hands the user a paste-ready block. Wiring = Slack on + the DM id set.
        if not systems.get("slack"):
            r.add(WARN, "machine", "lattice: enabled but `systems.slack` is off — the self-assessment skill reads the Lattice bot DM through Slack")
        elif identity_set("WORKSPACE_SLACK_LATTICE_DM"):
            r.add(OK, "machine", "lattice: paste-block flow — `WORKSPACE_SLACK_LATTICE_DM` set (plugin option or settings.local.json; no connector to probe)")
        else:
            r.add(WARN, "machine", "lattice: `WORKSPACE_SLACK_LATTICE_DM` unset (`/plugin configure ai-baton` or settings.local.json) — the self-assessment skill cannot read the Lattice bot DM")
    mcp = [k for k in kb.MCP_BACKED if systems.get(k)]  # kb.py owns the list, beside kb.SYSTEMS
    if mcp:
        r.raw(f"- MCP-backed systems ({', '.join(mcp)}): not probeable from a shell — the skill's step 3 checks the servers are connected in this session")
    return envname


# ── 5. engine ───────────────────────────────────────────────────────────────────────────────
def sec_engine(r: Report, stamping: bool = False) -> None:
    r.h("5 · Engine — smoke")
    # read-only: `make verify` never writes; a stale INDEX.md is a WARN with the fix named, not a silent rewrite of
    # the live DB — `--stamp` regenerates it after the stamp doc is written
    rc, out, err = sh(["make", "-C", str(KIT / "context-db"), "-s", f"CONTEXT={ctx()}", "verify"], timeout=300)
    # verify.py's problem list follows its `FAIL —` header; the ⚠ oversized-doc notes above it are `- ` lines too
    tail = err.split("FAIL —", 1)[1] if "FAIL —" in err else ""
    problems = [ln.strip()[2:] for ln in tail.splitlines() if ln.strip().startswith("- ")]
    if rc == 0:
        r.add(OK, "engine", "`make verify` on the live `.context/`: " + (out.splitlines()[-1] if out else "OK"))
    elif problems and all("INDEX.md is stale" in p for p in problems):
        r.add(WARN, "engine", "`.context/INDEX.md` is stale — `make -C $BATON/context-db index`" +
              (" (this run re-indexes after the stamp)" if stamping else " (a `--stamp` run does it; a plain run never writes the DB)"))
    else:
        r.add(ERR, "engine", "`make verify` failed:\n```\n" + both(out, err)[-1500:] + "\n```")
    for label, env in [(f"{kit_profile.name()} (env store)", {})]:
        rc1, doms, _ = sh([sys.executable, str(BIN / "kit_profile.py"), "domains"], env=env)
        rc2, tz, _ = sh([sys.executable, str(BIN / "kit_profile.py"), "tz"], env=env)
        rc3, kind, _ = sh([sys.executable, str(BIN / "kit_profile.py"), "get", "tracker.kind"], env=env)
        rc4, _, _ = sh([sys.executable, "-c", "import verify, session_stats"], cwd=BIN, env=env)
        if rc1 == rc2 == rc3 == rc4 == 0:
            r.add(OK, "engine", f"`{label}` loads: tracker `{kind}`, tz `{tz}`, domains {doms.split() or '[]'}")
        else:
            r.add(ERR, "engine", f"`{label}` does not load cleanly (kit_profile/verify/session_stats import)")
        with tempfile.TemporaryDirectory(prefix="kit-health-") as tmp:
            scratch = Path(tmp)
            first = (doms.split() or ["reference"])[0]
            for d in doms.split():
                (scratch / d).mkdir(parents=True, exist_ok=True)
            failed = []
            for t in ("epic", "reference", "repo", "meeting", "1on1", "oncall", "self-assessment", "pr-review", "log"):
                env2 = dict(env, CONTEXT_ROOT=str(scratch), TYPE=t, DOMAIN=first, SLUG=f"smoke-{t}", TITLE=f"smoke {t}")
                rc, out, err = sh(["sh", str(BIN / "new.sh")], env=env2)
                if rc != 0:
                    failed.append(f"{t}: {(err or out).splitlines()[-1] if (err or out) else rc}")
            if failed:
                r.add(ERR, "engine", f"`{label}`: new.sh failed for " + "; ".join(failed))
            else:
                r.add(OK, "engine", f"`{label}`: new.sh scaffolds all 9 doc types")
    rc, out, err = sh([sys.executable, str(BIN / "kb.py"), "list"])
    r.add(OK if rc == 0 else ERR, "engine", "`kb.py list` " + ("reads the env store" if rc == 0 else f"failed: {both(out, err)[-300:]}"))
    ctx_store(r)
    ctx_pin_check(r)


def ctx_pin_check(r: Report) -> None:
    """The ctx at the adapter's pin must itself answer `ctx --version` with the API `ctx_adapter.py version`'s
    second line names (`ctx_adapter.CTX_API`) — a machine whose cached install predates a pin bump, or whose
    `KIT_CTX` override points at an unrelated build, would otherwise drift from what the hooks (the PreToolUse
    deny, `validate --changed --adopt`) assume without kit-health ever saying so. Read-only: `where` finds the
    pinned executable (never installs it), then `<ctx> --version` is run directly — no adopted store needed."""
    adapter = "python3 $BATON/context-db/bin/ctx_adapter.py"
    fix = f"`{adapter} install && {adapter} adopt`"
    rc, out, err = sh([sys.executable, str(BIN / "ctx_adapter.py"), "version"])
    want = next((ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("api ")), "")
    if rc != 0 or not want:
        r.add(ERR, "engine", f"`{adapter} version` failed: {both(out, err)[-300:]}")
        return
    rc, out, err = sh([sys.executable, str(BIN / "ctx_adapter.py"), "where"])
    if rc != 0:
        r.add(WARN, "engine", f"ctx pin: not installed — {fix}")
        return
    ctx_path = out.strip()
    rc, out, err = sh([ctx_path, "--version"])
    if rc != 0:
        r.add(WARN, "engine", f"ctx pin: `{ctx_path} --version` failed: {both(out, err)[-300:]} — {fix}")
        return
    m = re.search(r"\bapi\s+(\d+)\b", out)
    got = m.group(1) if m else ""
    if got != want:
        # `install` keeps a usable pinned copy and KIT_CTX overrides the pin, so the plain fix would not clear this
        if os.environ.get("KIT_CTX", "").strip():
            how = f"`KIT_CTX` points at it — unset it, or point it at a ctx that reports api {want}"
        else:
            how = f"remove `{Path(ctx_path).parent}` (a stale copy at the pin), then {fix}"
        r.add(WARN, "engine", f"ctx pin: `{ctx_path} --version` reports api {got or 'none'}, the adapter expects "
                              f"api {want} — {how}")
    else:
        r.add(OK, "engine", f"ctx pin: `{ctx_path} --version` reports api {want}")


def ctx_store(r: Report) -> None:
    """Is `.context/` an adopted ctx store? Read-only (`ctx_adapter.py adopt --check`): the hooks validate, audit and
    deny direct writes only on an adopted store, so a store that is not is a finding with the one-time fix."""
    adapter = "python3 $BATON/context-db/bin/ctx_adapter.py"
    rc, out, err = sh([sys.executable, str(BIN / "ctx_adapter.py"), "adopt", "--check"], timeout=300)
    first = (out.splitlines() or [""])[0]
    if rc == 0:
        r.add(OK, "engine", f"ctx-store: `.context/` is an adopted store ({first.split(': ', 1)[-1]})")
    elif rc == 1:
        r.add(WARN, "engine", f"ctx-store is not installed, so writes under `.context/` are neither validated nor audited — "
                              f"`{adapter} install && {adapter} adopt`")
    elif rc == 4:
        r.add(WARN, "engine", f"store not adopted: `.context/` is not a ctx store, so the hooks neither validate nor route "
                              f"writes through ctx — `{adapter} adopt` (`ctx init`; a store file you changed is kept)")
    elif rc == 3:
        found = [ln[len("finding: "):] for ln in out.splitlines() if ln.startswith("finding: ")]
        r.add(WARN, "engine", f"ctx validate: {len(found)} finding(s) in the store — fix each with the ctx tools:\n```\n"
                              + "\n".join(found[:10]) + "\n```")
    else:
        r.add(ERR, "engine", f"`ctx_adapter.py adopt --check` failed: {both(out, err)[-300:]}")


# ── 6. stamp ────────────────────────────────────────────────────────────────────────────────
def read_health(envname: str) -> dict[str, str]:
    p = health_path(envname)
    return frontmatter(p) if p.is_file() else {}


def sec_stamp(r: Report, active: str) -> None:
    r.h("6 · Stamp — last green run of this environment")
    h = read_health(active)
    if not h:
        r.add(WARN, "stamp", f"`{active}`: never stamped — this run stamps it when green (`--stamp`)")
        return
    commit = h.get("kit_commit", "")
    rc, behind, _ = sh(["git", "-C", str(KIT), "rev-list", "--count", f"{commit}..HEAD"]) if commit else (1, "", "")
    n = int(behind) if rc == 0 and behind.isdigit() else None
    when = h.get("last_green", "?")
    head = kit_head()
    plugin = kit_profile.plugin_install(KIT) if n is None and commit and head else None
    if plugin is not None and commit != head:
        # a plugin install cannot count commits: a newer install this run re-stamps; GitHub names what changed (#3, #13)
        r.add(OK, "stamp", f"`{active}`: last green at `{commit[:7]}` ({when}), the installed kit is `{head[:7]}` — this run re-stamps it")
        changed = changed_units_remote(plugin["repo"], commit, head)
        unknown = (f"- changed units unknown (a plugin install has no git history, and GitHub's compare API did not answer: "
                   f"offline, or `{commit[:7]}` is not a commit of `{plugin['repo'] or '?'}`)")
    else:
        if plugin is not None:
            n = 0  # same commit = at HEAD
        if n == 0:
            r.add(OK, "stamp", f"`{active}`: green at `{commit[:7]}` = HEAD ({when}, {h.get('warnings', '?')} warnings"
                  f"{', ' + h['install_mode'] if h.get('install_mode') else ''})")
        elif n is None:
            r.add(WARN, "stamp", f"`{active}`: last green at `{commit[:7]}` ({when}) — commit unknown here (fetch origin)")
        else:
            r.add(OK, "stamp", f"`{active}`: last green at `{commit[:7]}` ({when}), HEAD is {n} kit commit(s) newer — this run re-stamps it")
        changed = [] if plugin is not None else (changed_units(commit) if commit else None)
        unknown = f"- changed units unknown (git could not diff `{commit[:7] or '?'}..HEAD`)"
    if changed:
        r.raw("- changed since that stamp (the judgement pass re-reads these): " + ", ".join(f"`{c}`" for c in changed))
    elif changed is None:
        r.raw(unknown + " — the judgement pass re-reads every unit the report flags")
    else:
        r.raw("- no skill, agent or WORKSPACE.md changed since that stamp — nothing to re-read")
    r.raw("- other environments keep their own stamp in their `.context/kit-health/` — run `/kit-health` there after every kit change")


def kit_version() -> str:
    """`v0.3.0` on a release commit, `v0.3.0+2` two commits after it, "" before the first release tag."""
    rc, d, _ = sh(["git", "-C", str(KIT), "describe", "--tags", "--match", "v[0-9]*", "--long"])
    if rc != 0 or not d:
        plugin = kit_profile.plugin_install(KIT)  # a plugin install: the manifest's release version (#3)
        return f"v{plugin['version']}" if plugin and plugin["version"] else ""
    tag, n, _ = d.rsplit("-", 2)
    return tag if n == "0" else f"{tag}+{n}"


def stamp(envname: str, r: Report) -> "tuple[Path, bool]":
    head = kit_head()
    rc = 0 if head else 1
    ver = kit_version()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    p = health_path(envname)
    p.parent.mkdir(parents=True, exist_ok=True)
    # a valid .context/ doc (title/type/domain/status/updated pass `make verify`) + the stamp fields
    p.write_text(
        "---\n"
        f"title: kit-health stamp — {envname}\n"
        "type: log\n"
        "domain: kit-health\n"
        "status: reference\n"
        "tags: [kit-health, stamp]\n"
        f"updated: {now[:10]}\n"
        f"environment: {envname}\n"
        f"last_green: {now}\n"
        f"kit_commit: {head if rc == 0 else 'unknown'}\n"
        f"kit_version: {ver or 'none'}\n"
        f"install_mode: {kit_profile.install_mode(KIT)}\n"
        f"warnings: {r.counts[WARN]}\n"
        "---\n\n"
        f"# kit-health — `{envname}`\n\n"
        f"Last green run: **{now}** on kit commit `{head[:7] if rc == 0 else '?'}`{f' ({ver})' if ver else ''}, installed as "
        f"`{kit_profile.install_mode(KIT)}`, with {r.counts[WARN]} warning(s) "
        "(written by `skills/kit-health/kit-health.py --stamp`, local to this machine). "
        "The report of that run is the `.context/` log doc the skill writes (`make -C $BATON/context-db new TYPE=log DOMAIN=kit-health …`); this stamp is local, not synced.\n\n"
        "\"The kit works everywhere\" = every environment's stamp is at the current kit HEAD; check each machine's stamp after a kit change.\n",
        encoding="utf-8",
    )
    # the stamp is a .context/ doc: re-index now, or the next run's `make verify index` reads it as stale
    rc, out, err = sh(["make", "-C", str(KIT / "context-db"), "-s", f"CONTEXT={ctx()}", "index"], timeout=300)
    if rc != 0:
        r.raw("\n⚠ `make index` after the stamp failed — run `make -C $BATON/context-db index` by hand:\n```\n" + both(out, err)[-800:] + "\n```")
        # the verdict is already built and --quiet prints only it: stderr + a non-zero exit keep this visible
        print(f"kit-health: `make index` after --stamp failed (rc={rc}) — run `make -C $BATON/context-db index`",
              file=sys.stderr)
    return p, rc == 0


def header_time(now: dt.datetime | None = None) -> str:
    """The report header's timestamp in the user's zone (`kit_profile.zone()`), e.g. `2026-09-26 21:04 EDT`, so the
    header and the log it is pasted into (`<YYYY-MM-DD>-<env>`, the local date) name the same day (#12). The stamp's
    `last_green` stays UTC."""
    z, note = kit_profile.zone()
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(z)
    return t.strftime("%Y-%m-%d %H:%M %Z") + note


def guarded(r: Report, section: str, fn, *args, default=None):
    """Run one section; a crash is a RED finding with its cause, and the run goes on to the next section — never a
    traceback that loses the report. Unexpected store content (a null config section, a stray table) lands here."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 — every section failure is reported, none swallowed
        import traceback
        where = traceback.extract_tb(e.__traceback__)[-1]
        r.add(ERR, section, f"section crashed: `{type(e).__name__}: {e}` at `kit-health.py:{where.lineno}` — the checks "
                            f"after that point did not run; fix the cause (often unexpected env-store content) and re-run")
        return default


def write_report(path: str | None, r: Report) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("\n".join(r.lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="kit-health: audit the kit on this machine (exit 0 GREEN · 1 AMBER · 2 RED)")
    ap.add_argument("--stale", type=int, default=90, metavar="DAYS",
                    help="a unit whose metadata.reviewed is older than DAYS is a WARN (default 90)")
    ap.add_argument("--stamp", action="store_true", help="write the HEALTH.md stamp for this environment when there are no errors")
    ap.add_argument("--report", help="also write the report to this file")
    ap.add_argument("--quiet", action="store_true", help="print only the summary line")
    ap.add_argument("--ci", action="store_true", help="CI mode: leak scan + env-store config only, against the checkout and the blank "
                                                       "store CI builds; nothing under .context/ is written; exit 2 on an error or a leak hit, else 0")
    a = ap.parse_args()
    r = Report()
    try:
        return run(a, r)
    finally:
        write_report(a.report, r)  # a crash outside every section still leaves the findings gathered so far


def run(a, r: Report) -> int:
    r.raw(f"# kit-health · {header_time()}" + (" · CI mode" if a.ci else ""))
    if a.ci:
        guarded(r, "leaks", sec_leaks, r)
        guarded(r, "config", sec_config, r)
        r.h("Summary")
        verdict = "RED" if r.counts[ERR] or r.leak_hits else "GREEN"
        summary = f"**{verdict}** (CI) — {r.counts[ERR]} errors · {r.leak_hits} leak hit(s) · {r.counts[WARN]} warnings · {r.counts[OK]} checks passed"
        r.raw(summary)
        print(summary if a.quiet else "\n".join(r.lines) + "\n")
        return 2 if (r.counts[ERR] or r.leak_hits) else 0
    guarded(r, "kit", sec_kit, r, a.stale)
    guarded(r, "leaks", sec_leaks, r)
    guarded(r, "config", sec_config, r)
    active = guarded(r, "machine", sec_machine, r, default="unknown")
    guarded(r, "engine", sec_engine, r, a.stamp)
    guarded(r, "stamp", sec_stamp, r, active)
    r.h("Summary")
    verdict = "RED" if r.counts[ERR] else ("AMBER" if r.counts[WARN] else "GREEN")
    summary = f"**{verdict}** — {r.counts[ERR]} errors · {r.counts[WARN]} warnings · {r.counts[OK]} checks passed · environment `{active}`"
    r.raw(summary)
    if r.findings:
        r.raw("\nFindings to walk (Fix / Ticket / Accept):")
        for i, (lvl, sec, text) in enumerate(r.findings, 1):
            r.raw(f"{i}. [{lvl}] ({sec}) {text.splitlines()[0]}")
    index_ok = True
    if a.stamp and may_stamp(r):
        p, index_ok = stamp(active, r)
        r.raw(f"\nStamped `{rel(p)}`.")
    elif a.stamp:
        r.raw("\nNot stamped — " + ("errors present." if r.counts[ERR] else
              f"{r.leak_hits} un-accepted leak hit(s): fix each, or accept it in `skills/kit-health/allow.txt` with a `# reason`."))
    print(summary if a.quiet else "\n".join(r.lines) + "\n")
    # the verdict is the exit code (0 GREEN · 1 AMBER · 2 RED); a stamp whose re-index failed is AMBER at worst
    return 2 if r.counts[ERR] else (1 if r.counts[WARN] or not index_ok else 0)


if __name__ == "__main__":
    raise SystemExit(main())
