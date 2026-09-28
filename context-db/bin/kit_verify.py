#!/usr/bin/env python3
"""Verify the kit itself — every skill and agent carries the versioning frontmatter, this
machine's env store is complete.

Checks `skills/*/SKILL.md` and `agents/*.md` (relative to the kit root, ../.. from here) against the
Agent Skills spec's "Claude Code profile" (docs/contributing.md § Skill frontmatter):
  - every `make -C $BATON/context-db <target>` a unit or kit doc cites is a Makefile target,
  - no kit file cites an issue number (`#n`) past this tracker's reach: the highest `issues/<n>` link in the
    release CHANGELOG plus ISSUE_REF_MARGIN (a number from another tracker would resolve to an unrelated issue),
  - only spec keys (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`) and the
    allow-listed Claude Code native keys at the top level (NATIVE_KEYS; agents also AGENT_KEYS) — any other
    key fails, and the kit's own `version`/`updated`/`reviewed`/`requires`/`facts` at the top level fail with
    a pointer to `migrate_frontmatter.py`,
  - `name` = the skill's directory / the agent's file stem,
  - `metadata:` holding version (integer), updated + reviewed (YYYY-MM-DD) as double-quoted strings,
  - optional metadata.requires (comma-separated known capability flags) — the only gate a unit carries —
    with a `compatibility:` line naming every flag, so a spec-only reader sees the gate too,
  - optional metadata.facts (comma-separated `<system>.<kind>[ <name>]` / `<config.key>` entries, each covered
    by a discovery manifest in context-db/discovery/ — the manifests themselves are validated too), and every
    fact the body itself reads via `kit_profile.py get` / `kb.py get` must be named there too — a body that
    reads an undeclared fact fails here, not the first time a session hits it mid-skill,
  - no `environments:` tag and no "(<name>) " description prefix: the kit never names an
    environment (retired 2026-09-25; which machine has which capability is its local env store),
  - description ≤ 60 whitespace-separated tokens and ≤ 400 B, all descriptions ≤ 9,500 B together (warns past
    90% of the total budget, naming the largest) — every description loads into every session's prefix,
  - every unquoted scalar value is a valid YAML plain scalar (frontmatter.plain_scalar_problem — a leading
    indicator character, ': ', ' #' or a trailing ':' would make a real YAML parser refuse the file even
    though this module's own lenient parser reads it fine) — quote it instead,
  - an agent's body never spells the fork hand-back line with a colon (`NEEDS:`) — the one spelling is
    `NEEDS <system>.<kind> <name>` (docs/env-facts.md § Environment facts).
And the env store (`.context/reference/env/config.json`, see kb.py — one per machine, not in the
kit) for every top-level key `environment-template/config.json` has, `systems.*` covering every
flag a skill may `require` as booleans, a compiling one-group `tracker.key_regex`, and no rows left
under a renamed kind (`kb.py migrate` moves them).
  - the body: warns past `BODY_WARN_LINES` (130, docs/authoring.md's target) and errors past `BODY_MAX_LINES`
    (300) — a unit named in `BODY_LINES_ALLOW` with a reason skips the warning, never the cap — every `scripts/…` /
    `references/…` / `$BATON/skills/<name>/…` path it cites exists, and no generic environment-fact shape
    (Slack id, custom-field id, account id, ticket key, org host, tz literal — `leak_shapes.py`, shared with
    kit-health) in the file.
  - docs/loading.md's `<!-- kit-verify:<key> -->` numbers must match what this run just computed (`--loading-table`
    prints them without running the rest of the checks).
With --stale N also lists units whose `reviewed` is older than N days (warning, not an error). Two more
non-blocking notes, always on: a description that names no trigger ("Use when …", or plain "when …") or reads
in the first person, and a `reviewed:` date older than the file's last committed edit (`--no-git` skips the
latter — no git history to compare against).

`--no-env` is the ENVIRONMENT-FREE validator: everything above except the env-store checks, which are
skipped and listed — it passes on a bare `git clone` with no `.context/` at all, so a contributor's PR (and CI,
before it builds a blank store) can run it. Positional paths verify only those units (`make -C $BATON/context-db
verify-skill UNIT=skills/<name>`): the kit-wide totals (always-on budget, description total) are skipped too.
Run via `make -C $BATON/context-db kit-verify`. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import frontmatter as fmt
import kb
import kit_profile
import leak_shapes

KIT = Path(__file__).resolve().parents[2]
OPTIONAL = kb.OPTIONAL_CONFIG_KEYS  # one list: kb.py owns it, `kb.config_key_drift()` checks blank_config() against the template
SYSTEMS = set(kb.SYSTEMS)  # capability flags a unit may `requires:` (kb.py owns the list)
DATE = "%Y-%m-%d"

# The Claude Code profile of the Agent Skills spec (agentskills.io/specification): spec keys plus the Claude Code
# native keys the kit relies on — each listed with its reason in docs/contributing.md § Skill frontmatter. A new
# native key is added here AND there, with a justification in the PR; anything else is a failure.
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NATIVE_KEYS = {"model", "effort", "context", "agent", "user-invocable", "argument-hint", "arguments", "background"}
AGENT_KEYS = {"tools", "disallowedTools", "color", "omitClaudeMd", "maxTurns"}  # agents only
META_REQUIRED = ("version", "updated", "reviewed")
RETIRED_KEYS = ("environments", "profiles")

# `kb.ENV` override for --no-env: a path that is never a directory, so `load_manifests()` (and everything built
# on it — `fact_entries()`, `find_fact()`) only sees the kit's own `context-db/discovery/*.json` and never a
# machine's `_discovery/` overlay — a store's overlay is that machine's business, reported by kit-health, never
# by the environment-free validator (a bare clone, a contributor's PR, CI).
NO_ENV_STORE = Path("/nonexistent-env-store")


def load_json_file(p: Path) -> tuple[dict | None, str | None]:
    """Read + parse one JSON file that is not guaranteed to be well-formed or even reachable — returns
    (data, None) or (None, reason). Every way a file can misbehave (bad JSON, a non-UTF-8 byte, an unreadable
    or dangling path) is one finding, never a traceback that looks like a real failure in CI logs. The reason
    is pre-labelled — `unreadable` for a path that could not even be opened (OSError: missing, dangling
    symlink, permissions), `invalid JSON` for a path that opened but did not parse (bad JSON, a non-UTF-8
    byte) — so a caller never has to guess which kind of failure it is passing on."""
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except OSError as e:
        return None, f"unreadable — {e}"
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, f"invalid JSON — {e}"


def check_env_store(errors: list[str]) -> str:
    """Validate this machine's env store config against the template's key set. Returns the
    environment name ("" when there is no store)."""
    want_keys = kb.template_config_keys()
    p = kit_profile.ENV_DIR / "config.json"
    if not p.is_file():
        errors.append(f"no configuration: env store {p} missing — run `python3 $BATON/context-db/bin/kb.py init --blank`")
        return ""
    rel = f"{p.parent.name}/{p.name}"
    cfg, err = load_json_file(p)
    if err is not None:
        errors.append(f"{rel}: {err}")
        return ""
    # optional keys: absent means the kit default (commit_style.py falls back to `conventional`)
    for k in sorted(want_keys - set(cfg) - {"environment"} - OPTIONAL):
        errors.append(f"{rel}: missing top-level key '{k}' (see environment-template/config.json)")
    envname = str(cfg.get("environment") or "").strip()
    if not envname:
        errors.append(f"{rel}: 'environment' must name this environment (`kb.py config-set environment <name>`)")
    elif not re.fullmatch(r"[a-z][a-z0-9-]*", envname):
        errors.append(f"{rel}: 'environment' '{envname}' is not a lowercase slug")
    systems = cfg.get("systems")
    if systems is None:
        systems = {}  # like `tracker: null`: nothing set yet — `kb.py migrate` adds every flag (one line below)
    if not isinstance(systems, dict):
        errors.append(f"{rel}: systems must be an object of true/false flags, got {systems!r}")
    else:
        have = set(systems)
        renamed = sorted(o for o in kb.RENAMED_SYSTEMS if o in have)
        if renamed:
            errors.append(f"{rel}: renamed system flag(s) {', '.join('systems.' + o for o in renamed)} — "
                          "run `python3 $BATON/context-db/bin/kb.py migrate` (keeps the value)")
        missing = sorted(SYSTEMS - have)
        if missing:
            errors.append(f"{rel}: {', '.join('systems.' + s_ for s_ in missing)} missing (true/false) — "
                          "`python3 $BATON/context-db/bin/kb.py migrate` adds them as false; set true what this machine has")
        for s_, v in systems.items():
            if not isinstance(v, bool):
                errors.append(f"{rel}: systems.{s_} must be true/false")
    commits = cfg.get("commits")
    styles = ("conventional", "ticket-key", "free")
    if commits is None:
        commits = {}
    elif not isinstance(commits, dict):
        errors.append(f"{rel}: commits must be an object {{default, repos}}")
        commits = {}
    if commits.get("default", "conventional") not in styles:
        errors.append(f"{rel}: commits.default must be one of {', '.join(styles)} (kit default: conventional)")
    repos_ = commits.get("repos", {})
    if not isinstance(repos_, dict):
        errors.append(f"{rel}: commits.repos must be an object {{\"<owner/repo>\": style}}")
        repos_ = {}
    for repo_, s_ in repos_.items():
        if s_ not in styles:
            errors.append(f"{rel}: commits.repos[{repo_}] = '{s_}' is not one of {', '.join(styles)}")
    for (s_, old), (_, new) in kb.RENAMED_KINDS.items():
        if kb.parse_doc(s_)[1].get(old):
            errors.append(f"{s_}.md: rows under `## {old}` belong under `## {new}` — run "
                          "`python3 $BATON/context-db/bin/kb.py migrate` (moves them, keeps the cells)")
    for problem in kb.close_reason_problems(cfg):
        errors.append(f"{rel}: {problem} — run `python3 $BATON/context-db/bin/kb.py migrate`")
    tracker = cfg.get("tracker")
    if tracker is None:
        tracker = {}
    elif not isinstance(tracker, dict):
        errors.append(f"{rel}: tracker must be an object {{kind, key_regex, …}} (see environment-template/config.json), got {tracker!r}")
        tracker = {}
    kind = tracker.get("kind")
    if kind is not None and kind not in kit_profile.TRACKER_KINDS:
        errors.append(f"{rel}: tracker.kind '{kind}' is not one of {', '.join(kit_profile.TRACKER_KINDS)}")
    rx = tracker.get("key_regex")
    if kind not in ("none", None) and not rx:
        errors.append(f"{rel}: tracker.key_regex missing")
    if rx:
        try:
            if re.compile(rx).groups != 1:
                errors.append(f"{rel}: tracker.key_regex must have exactly one capture group")
        except re.error as e:
            errors.append(f"{rel}: tracker.key_regex does not compile — {e}")
    return envname


def validate_manifests_kit() -> list[str]:
    """The kit's discovery manifests must parse and follow the schema (the store's `_discovery/` overlays are
    the machine's business — reported by kit-health, not here)."""
    saved = kb.ENV
    try:
        kb.ENV = NO_ENV_STORE  # only the kit's own manifests
        return [e.replace(str(KIT) + "/", "") for e in kb.validate_manifests()]
    finally:
        kb.ENV = saved


# Always-on prefix budget (bytes): these files load into every session's cached prefix, so their size is
# re-billed on every turn. WORKSPACE.md was 25 KB before its trim; the cap sits deliberately just above the
# trimmed size so every addition must earn its bytes. The template cap followed its own trim (2.0 KB).
ALWAYS_ON_BUDGET = {
    "CLAUDE.md": 600,  # the kit's own memory file: Claude Code loads .claude/CLAUDE.md into every workspace session too
    "WORKSPACE.md": 10_000,
    "environment-template/environment.md": 2_200,
}


PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"


def check_plugin_manifest(errors: list[str]) -> None:
    """The plugin manifest: parses, kebab-case `name`, `version` == `VERSION` (conventional-release bumps both;
    the marketplace entry names the same plugin and pins a github source to that same version, tag-prefixed, so
    an install fetches the tagged release rather than whatever the default branch holds — `bump_marketplace_ref.py`
    keeps `ref` in step with a release; this is the drift gate for when that step is skipped or edited by hand).
    `claude plugin validate --strict .` is the authoritative schema check (make plugin-validate); this is the
    version discipline no external tool checks."""
    mp = KIT / PLUGIN_MANIFEST
    if not mp.is_file():
        errors.append(f"{PLUGIN_MANIFEST}: missing — the kit is a plugin (docs/packaging.md)")
        return
    man, err = load_json_file(mp)
    if err is not None:
        errors.append(f"{PLUGIN_MANIFEST}: {err}")
        return
    name = str(man.get("name") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        errors.append(f"{PLUGIN_MANIFEST}: name {name!r} is not kebab-case (lowercase letters, digits, hyphens)")
    version = str(man.get("version") or "")
    vfile = KIT / "VERSION"
    kit_version = vfile.read_text(encoding="utf-8").strip() if vfile.is_file() else ""
    if not kit_version:
        errors.append("VERSION: missing or empty (the kit's version file, conventional-release bumps it)")
    elif version != kit_version:
        errors.append(f"{PLUGIN_MANIFEST}: version {version!r} != VERSION {kit_version!r} — the two are bumped together "
                      "(`.conventional-release.toml` version-files)")
    mk = KIT / MARKETPLACE_MANIFEST
    if not mk.is_file():
        errors.append(f"{MARKETPLACE_MANIFEST}: missing — the repo is its own marketplace (docs/packaging.md)")
        return
    market, err = load_json_file(mk)
    if err is not None:
        errors.append(f"{MARKETPLACE_MANIFEST}: {err}")
        return
    entries = market.get("plugins") if isinstance(market.get("plugins"), list) else []
    entry = next((e for e in entries if isinstance(e, dict) and e.get("name") == name), None)
    if entry is None:
        errors.append(f"{MARKETPLACE_MANIFEST}: no plugins[] entry with name {name!r}")
    else:
        source = entry.get("source")
        if not (isinstance(source, dict) and source.get("source") == "github" and source.get("repo")):
            errors.append(f"{MARKETPLACE_MANIFEST}: plugins[].source for {name!r} is not a github source "
                           "(an install must resolve to the tagged release, not the repo's default branch)")
        elif kit_version and source.get("ref") != f"v{kit_version}":
            errors.append(f"{MARKETPLACE_MANIFEST}: source.ref {source.get('ref')!r} != 'v{kit_version}' — "
                           "the marketplace ref and plugin.json/VERSION drifted (bump_marketplace_ref.py keeps "
                           "them together at release time)")
    check_identity_options(errors, man)


PLUGIN_HOOKS = "hooks/hooks.json"


def check_identity_options(errors: list[str], man: dict) -> None:
    """Identity via plugin userConfig: every `WORKSPACE_*` variable `kit_profile.IDENTITY_KEYS` resolves has a
    `userConfig` entry of the mapped key (string, titled, described) and nothing else is declared there — the
    manifest and the resolver drift apart otherwise; and `hooks/hooks.json` carries the SessionStart hook that
    re-exports the options as `WORKSPACE_*` and `CLAUDE_PROJECT_DIR` for Bash (`kit_profile.py session-env`, #3), else the plugin path
    never sees the values the user typed into `/plugin configure ai-baton`."""
    uc = man.get("userConfig") if isinstance(man.get("userConfig"), dict) else {}
    want = {v: k for k, v in kit_profile.IDENTITY_KEYS.items()}
    for key, var in sorted(want.items()):
        entry = uc.get(key)
        if not isinstance(entry, dict):
            errors.append(f"{PLUGIN_MANIFEST}: userConfig lacks `{key}` (carries {var}; kit_profile.IDENTITY_KEYS)")
            continue
        if entry.get("type") != "string" or not entry.get("title") or not entry.get("description"):
            errors.append(f"{PLUGIN_MANIFEST}: userConfig.{key} needs type \"string\", a title and a description")
    for key in sorted(set(uc) - set(want)):
        errors.append(f"{PLUGIN_MANIFEST}: userConfig.{key} has no WORKSPACE_* mapping in kit_profile.IDENTITY_KEYS — add one or drop it"
                      " (non-secret environment facts belong in the env store, not userConfig)")
    hp = KIT / PLUGIN_HOOKS
    if not hp.is_file():
        errors.append(f"{PLUGIN_HOOKS}: missing — the SessionStart hook that exports plugin options as WORKSPACE_*")
        return
    hooks, err = load_json_file(hp)
    if err is not None:
        errors.append(f"{PLUGIN_HOOKS}: {err}")
        return
    cmds = [h.get("command", "") for grp in (hooks.get("hooks", {}).get("SessionStart") or []) if isinstance(grp, dict)
            for h in (grp.get("hooks") or []) if isinstance(h, dict)]
    if not any("session-env" in c and "CLAUDE_ENV_FILE" in c and "${CLAUDE_PLUGIN_ROOT}" in c for c in cmds):
        errors.append(f"{PLUGIN_HOOKS}: no SessionStart hook runs `${{CLAUDE_PLUGIN_ROOT}}/…/kit_profile.py session-env` into $CLAUDE_ENV_FILE")
    if not any("workspace-rules" in c and "${CLAUDE_PLUGIN_ROOT}" in c for c in cmds):
        errors.append(f"{PLUGIN_HOOKS}: no SessionStart hook prints `kit_profile.py workspace-rules` — a plugin install "
                      "would load no always-on rules (#3)")


MAKE_TARGET = re.compile(r"make(?:\s+-s)?\s+-C\s+(?:\.claude|\$BATON)/context-db(?:\s+-s)?\s+([a-z][a-z_-]*)\b")
MAKEFILE = KIT / "context-db" / "Makefile"
DOC_FILES = ("README.md", "CONTRIBUTING.md", "WORKSPACE.md", "docs")


def make_targets() -> set[str]:
    """Targets the engine Makefile defines (`name:` at the start of a line)."""
    if not MAKEFILE.is_file():
        return set()
    return {m.group(1) for m in re.finditer(r"^([a-z][a-z_-]*):", MAKEFILE.read_text(encoding="utf-8"), re.M)}


def cited_make_targets(text: str) -> list[tuple[int, str]]:
    """(line, target) for every `make -C $BATON/context-db <target>` a text cites."""
    return [(n, m.group(1)) for n, line in enumerate(text.split("\n"), 1) for m in MAKE_TARGET.finditer(line)]


def check_make_targets(errors: list[str], files=None) -> None:
    """A doc or unit that cites `make -C $BATON/context-db <target>` names a target the Makefile has:
    a session following the instruction otherwise stops on `No rule to make target`."""
    have = make_targets()
    if not have:
        return
    paths = list(files) if files is not None else [KIT / f for f in DOC_FILES[:3]] + sorted((KIT / "docs").rglob("*.md"))
    for p in paths:
        if not p.is_file():
            continue
        rel = p.relative_to(KIT) if p.is_relative_to(KIT) else p
        for n, target in cited_make_targets(p.read_text(encoding="utf-8", errors="replace")):
            if target not in have:
                errors.append(f"{rel}:{n}: cites `make -C $BATON/context-db {target}` but the Makefile has no such target "
                              f"(targets: {', '.join(sorted(have))})")


# `#n` citations in kit files. The ceiling is offline and moves with each release: the highest issue/PR the release
# CHANGELOG links, plus a margin for what is opened between releases. Backticked `#n` are examples; the release CHANGELOG is
# history; tests and evals carry fixture numbers.
ISSUE_REF = re.compile(r"(?<![\w/&#])#([1-9]\d{0,3})\b")
ISSUE_REF_MARGIN = 30
ISSUE_REF_SUFFIXES = {".md", ".py", ".sh", ".yml", ".yaml", ".json", ".mk"}
ISSUE_REF_SKIP = ("CHANGELOG.md", "context-db/tests/", "evals/")
ISSUE_REF_SKIP_DIRS = {".git", ".worktrees", "__pycache__", "node_modules"}


def kit_files(root: Path, skip_dirs: set[str]) -> list[Path]:
    """Every file under `root`, sorted; a directory named in `skip_dirs` (`.git`, `.worktrees`, `node_modules`, …)
    is pruned before the walk enters it, and a file so named (a worktree's `.git`) is left out too."""
    out: list[Path] = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip_dirs]
        out += [Path(d) / f for f in files if f not in skip_dirs]
    return sorted(out)


def issue_ref_ceiling(kit: Path = KIT) -> int:
    """The highest `issues/<n>` the release CHANGELOG links, plus the margin; 0 when there is no CHANGELOG."""
    p = kit / "CHANGELOG.md"
    if not p.is_file():
        return 0
    nums = [int(n) for n in re.findall(r"/issues/(\d+)\)", p.read_text(encoding="utf-8", errors="replace"))]
    return max(nums, default=0) + ISSUE_REF_MARGIN


def check_issue_refs(errors: list[str], kit: Path = KIT) -> None:
    """A kit file that cites `#n` above the ceiling cites another tracker's issue (the pre-public tracker's numbers
    once filled 165 lines): name the issue that tracks the topic now, or keep the reason in words."""
    ceiling = issue_ref_ceiling(kit)
    if not ceiling:
        return
    for p in kit_files(kit, ISSUE_REF_SKIP_DIRS):
        rel = p.relative_to(kit).as_posix()
        if (not p.is_file() or (p.suffix not in ISSUE_REF_SUFFIXES and p.name != "Makefile")
                or rel.startswith(ISSUE_REF_SKIP)):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            for m in ISSUE_REF.finditer(re.sub(r"`[^`]*`", "", line)):
                if int(m.group(1)) > ceiling:
                    errors.append(f"{rel}:{n}: cites #{m.group(1)}, past this tracker's highest issue (ceiling {ceiling}: "
                                  f"CHANGELOG.md + {ISSUE_REF_MARGIN}) — link the issue that tracks it here, or say it in words")


def check_workspace_kit_paths(errors: list[str]) -> None:
    """WORKSPACE.md loads into every session on both install paths: its commands reach the kit as `$BATON/…` (#3)."""
    p = KIT / "WORKSPACE.md"
    if not p.is_file():
        return
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
        for m in HARDCODED_KIT_PATH.finditer(line):
            errors.append(f"WORKSPACE.md:{n}: cites `{m.group(0)}` — write `$BATON/…`, the kit root on both install paths (#3)")


INSTALL_ONE_LINER = re.compile(r"claude plugin list --json\s*\|\s*python3 -c")


def check_readme_install(errors: list[str], kit: Path = KIT) -> None:
    """The README's install block is what a newcomer runs first: no `claude plugin list --json | python3 -c …` lookup of
    the plugin root there — `/kit-setup` runs setup.sh from inside the session (#97). docs/plugin-setup.md keeps the
    manual form in prose as the fallback."""
    p = kit / "README.md"
    if not p.is_file():
        return
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
        if INSTALL_ONE_LINER.search(line):
            errors.append(f"README.md:{n}: the plugin install looks up the plugin root with a python3 one-liner — use `/kit-setup` (#97)")


LOADING_MD = KIT / "docs" / "loading.md"
LOADING_MARKER = re.compile(r"<!-- kit-verify:(\w[\w-]*) -->(.*?)<!-- /kit-verify:\1 -->", re.S)


def loading_numbers() -> dict[str, int]:
    """The numbers `docs/loading.md` § Layer 1 / Layer 2 quotes: every unit and its description bytes (layer 1),
    every skill body and its bytes (layer 2) — one source both `--loading-table` and the drift check read, so
    the two can never say something different from what kit-verify itself just computed."""
    units = fmt.units(KIT)
    desc_bytes = 0
    body_bytes = 0
    bodies = 0
    for p in units:
        parts = fmt.split(p.read_text(encoding="utf-8", errors="replace"))
        fm = fmt.parse_lines(parts[0]) if parts else None
        d = fmt.unquote(fm.get("description", "")) if fm else ""
        desc_bytes += len(d.encode())
        if p.parent.name != "agents":
            bodies += 1
            body_bytes += len((parts[1] if parts else "").encode())
    return {"units": len(units), "desc_bytes": desc_bytes, "bodies": bodies, "body_bytes": body_bytes}


def loading_table_values(n: dict[str, int] | None = None) -> dict[str, str]:
    """The exact text each `<!-- kit-verify:<key> -->` marker in docs/loading.md must hold."""
    n = n or loading_numbers()
    return {
        "units": str(n["units"]),
        "desc-bytes": f"{n['desc_bytes']:,} B",
        "bodies": str(n["bodies"]),
        "body-bytes": f"{n['body_bytes']:,} B",
    }


LOADING_BYTES_TOLERANCE = 0.05  # byte totals move with nearly every skill edit; counts must match exactly


def _within_tolerance(key: str, have: str, want: str) -> bool:
    """A byte total within 5% of the measured one still tells the reader the right size: an exact match would fail
    every PR that edits a body and make parallel PRs conflict on the doc."""
    if not key.endswith("-bytes"):
        return False
    try:
        h, w = (int(v.replace(",", "").replace("B", "").strip()) for v in (have, want))
    except ValueError:
        return False
    return abs(h - w) <= LOADING_BYTES_TOLERANCE * w


def write_loading_table(values: dict[str, str], *, force: bool = False) -> dict[str, str]:
    """Rewrite a marked span in docs/loading.md only when the drift check would otherwise fail on it: a count
    that differs, or a byte total outside LOADING_BYTES_TOLERANCE — the same test `check_loading_table_drift`
    runs. A byte total within tolerance is left byte-identical, so a PR that only edits a skill body does not
    also touch docs/loading.md and collide with a parallel one on the same lines. `force=True` (the CLI's
    `--write --force`, for a deliberate refresh e.g. at release time) rewrites every marker regardless.
    Markers the doc lacks stay missing (the drift check still reports them). Returns a note per marker whose
    value differed from what's now measured (a marker that matches exactly has no entry) for the CLI to print alongside it."""
    text = LOADING_MD.read_text(encoding="utf-8")
    notes: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        key, old = m.group(1), m.group(2)
        want = values.get(key)
        if want is None or old == want:
            return m.group(0)
        if not force and _within_tolerance(key, old, want):
            pct = int(LOADING_BYTES_TOLERANCE * 100)
            notes[key] = f"(doc {old}, within {pct}%, left as is)"
            return m.group(0)
        notes[key] = "(rewritten)"
        return f"<!-- kit-verify:{key} -->{want}<!-- /kit-verify:{key} -->"

    new = LOADING_MARKER.sub(repl, text)
    if new != text:
        tmp = LOADING_MD.with_name(LOADING_MD.name + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, LOADING_MD)
    return notes


def check_loading_table_drift(errors: list[str]) -> None:
    """docs/loading.md quotes these numbers in prose; `<!-- kit-verify:<key> --><!-- /kit-verify:<key> -->` marks
    the exact span so a later PR's description or a new skill's body cannot leave it stale — the same
    discipline `pr-open`'s diagram set uses against the diff. Missing markers are reported once, not
    silently accepted (a doc that has not adopted the markers yet still needs updating by hand)."""
    if not LOADING_MD.is_file():
        return
    text = LOADING_MD.read_text(encoding="utf-8")
    have = {m.group(1): m.group(2) for m in LOADING_MARKER.finditer(text)}
    for key, want in loading_table_values().items():
        if key not in have:
            errors.append(f"docs/loading.md: no <!-- kit-verify:{key} --> marker — add one around the number and "
                          "re-run `kit_verify.py --loading-table --write`")
        elif have[key] != want and not _within_tolerance(key, have[key], want):
            errors.append(f"docs/loading.md: <!-- kit-verify:{key} --> says {have[key]!r}, kit-verify now computes "
                          f"{want!r} — run `kit_verify.py --loading-table --write`")


def check_always_on_budget(errors: list[str]) -> None:
    for rel, cap in ALWAYS_ON_BUDGET.items():
        p = KIT / rel
        if not p.exists():
            errors.append(f"{rel}: missing (always-on file)")
            continue
        # checkout-independent: a core.autocrlf clone must not fail with nothing to fix
        size = len(p.read_bytes().replace(b"\r\n", b"\n"))
        if size > cap:
            errors.append(f"{rel}: {size} bytes > {cap} always-on budget — this file loads into every session; "
                          "trim it or move detail into a skill/doc that loads on demand")


# Every unit's description is injected into every session's prefix whether or not the unit is used.
# A description is a trigger (when to invoke, what it returns, what it never does), not the procedure.
DESC_MAX_WORDS = 60
DESC_MAX_BYTES = 400
DESC_TOTAL_BYTES = 9_500
DESC_TOTAL_WARN_RATIO = 0.90  # warn this far into the budget so a new skill's description does not fail the gate as a surprise


def check_description(rel, fm, errors: list[str]) -> int:
    d = fmt.unquote(fm.get("description", ""))
    words, size = len(d.split()), len(d.encode())
    if words > DESC_MAX_WORDS:
        errors.append(f"{rel}: description is {words} words (whitespace tokens) > {DESC_MAX_WORDS} — it loads into every session; "
                      "keep the trigger, the return and the one thing it never does, drop the procedure")
    if size > DESC_MAX_BYTES:
        errors.append(f"{rel}: description is {size} bytes > {DESC_MAX_BYTES}")
    return size


# A description is what loads into every session's prefix; a reader (human or the trigger evals) must be able
# to tell WHEN a unit fires from it alone. Warn only for now — about a third of the units predate this rule
# and a wording pass on every one of them is a separate, editorial piece of work; the next release
# turns this into a failure (docs/authoring.md § the frontmatter table says so).
FIRST_PERSON = re.compile(r"\b(I|I'm|I've|my|our|myself)\b")


def check_description_shape(rel, fm, warn: list[str]) -> None:
    d = fmt.unquote(fm.get("description", ""))
    if "when" not in fmt.trigger_clause(d).lower():
        warn.append(f"{rel}: description names no trigger (\"Use when …\", \"Invoke when …\", or plain \"when …\") — "
                     "a reader cannot tell when to load this unit from the description alone (a \"when\" only in "
                     "its \"Not for …\" clause does not count)")
    if FIRST_PERSON.search(d):
        warn.append(f"{rel}: description reads in the first person — write it as the unit's own trigger, not prose about writing one")


def check_reviewed_freshness(kit: Path, rel, reviewed: str, warn: list[str]) -> None:
    """`reviewed:` is a claim that someone read the unit as of that date — stale the moment the file changes
    after it without a bump. `git log -1` on the committed history is the only source that is not itself
    `reviewed:` (a unit cannot attest its own freshness); a path git has never heard of (a new file, or no
    repository at all — a temp fixture in a test) answers nothing, so it is silently skipped, never guessed at."""
    try:
        out = subprocess.run(["git", "-C", str(kit), "log", "-1", "--format=%cs", "--", str(rel)],
                              capture_output=True, text=True, timeout=10)
    except OSError:
        return
    last = out.stdout.strip()
    if out.returncode != 0 or not last:
        return
    if last > reviewed:
        warn.append(f"{rel}: reviewed {reviewed} predates the last edit {last} — bump metadata.reviewed "
                     "(or metadata.version too, if the content itself changed)")


# `kit_profile.py get <key>` / `kb.py get <key>` — the one read API every unit body uses (docs/env-facts.md):
# a body that reads a fact the reader never sees named in `metadata.facts` hits it for the first time mid-skill,
# not at kit-verify time. `[\w.]*` stops at the first character that is neither a word char nor a dot, so
# a placeholder like `get systems.<x>` or `get tracker.close_reasons.<done|wont_do|cancelled>` is captured up to
# the dot before the `<` — exactly the prefix a caller needs to declare.
GET_CALL = re.compile(r"(?:kit_profile|kb)\.py\s+get\s+([A-Za-z][\w.]*\.?)")
SYSTEMS_FLAG_MENTION = re.compile(r"`systems\.([a-z_]+)`")


def audit_facts_read(rel, body: str, declared: set[str], errors: list[str]) -> None:
    seen_dynamic = False
    reported: set[str] = set()
    for gm in GET_CALL.finditer(body):
        line = body.count("\n", 0, gm.start()) + 1
        key = gm.group(1).rstrip(".")
        if "." not in key:
            if key != "systems" or seen_dynamic:
                continue  # a bare whole-section read (`get self_assessment`) — nothing single to declare
            seen_dynamic = True  # `get systems.<x>` — a dynamic gate; every concrete flag it names elsewhere must be declared
            for fm2 in SYSTEMS_FLAG_MENTION.finditer(body):
                fkey = f"systems.{fm2.group(1)}"
                if fkey not in declared and fkey not in reported:
                    reported.add(fkey)
                    errors.append(f"{rel}: reads `{fkey}` (env-gated behind a dynamic `get systems.<x>`, body line {line}) "
                                  "but does not declare it in metadata.facts")
            continue
        if key in declared or key in reported:
            continue
        reported.add(key)
        errors.append(f"{rel}: body line {line} reads env fact `{key}` (`kit_profile.py get` / `kb.py get`) "
                      "but does not declare it in metadata.facts")


BODY_WARN_LINES = 130  # docs/authoring.md's target for a body — past this, warn to move detail into references/
BODY_MAX_LINES = 300  # the hard cap — errors past this for every unit, allow-listed or not

# A unit whose body legitimately needs to stay past BODY_WARN_LINES because the long form IS the procedure, not
# reference material a step loads on demand — `fmt.unit_name(p)` (a skill's directory, an agent's file stem) to
# its reason. Exempts the unit from the BODY_WARN_LINES warning only: BODY_MAX_LINES still applies, so an
# allow-listed body cannot grow without bound.
BODY_LINES_ALLOW = {
    "pr-review": "the per-finding walk (Post / Deep dive / Body only / Skip), the repo trap KB and the "
                 "verification steps are the one walkthrough a reviewer follows top to bottom, not reference "
                 "material to split out",
    "self-assessment": "the week-file, report-block and ledger-card templates and the back-fill branch for "
                       "every systems-of-record source are the procedure itself, not reference material to "
                       "split out",
}
# a backticked path a unit cites inside its own directory: `scripts/x.sh …`, `references/y.md`, `.claude/skills/<name>/scripts/x`
# `skills/<x>/SKILL.md` or a bare `skills/<x>/` in a body: another skill referenced by PATH. The contract is by name
# (docs/contributing.md § Skills) — installed paths differ per host and a plugin root moves on every update. A script or
# reference file of another skill that a step runs (`skills/<x>/scripts/…`) is the one allowed path form.
CROSS_SKILL = re.compile(r"(?<![\w/-])(?:~/|\$HOME/|\$\{HOME\}/)?(?:\.claude/|\$BATON/)?skills/([\w-]+)/(?:SKILL\.md|README\.md)?(?=[`\s)'\"]|$)")
OWN_PATH = re.compile(r"`(?:[\w.-]+\s+)?((?:(?:\.claude|\$BATON)/(?:skills/[\w-]+|agents)/)?(?:scripts|references|reference)/[\w./-]+)")

# A kit path spelled the clone way. Not the workspace's own `.claude/` (settings.local.json, the sign-queue state dir),
# `~/.claude/` (the harness) or a repo's `<repo>/.claude/commit-style` — the lookbehind skips a path segment before it.
HARDCODED_KIT_PATH = re.compile(r"(?<![~\w>*/.$-])\.claude/(?:context-db|skills/|agents/|docs/|hooks/|README\.md|setup\.sh|"
                                r"environment-template/)[\w./<>-]*")


# A skill forked to a read-only agent (agents/<name>.md whose disallowedTools denies both Edit and Write —
# `triage`, today) must never mutate state through Bash either: `sed -i`, a shell append or `make … index`
# in its own body is the tool restriction defeated by another door (the very bug this check exists to
# catch — a fork that wrote STATE/KB straight through Bash while its docs called it read-only). The write
# belongs to whichever session applies the fork's return value, never a step the fork runs itself.
FORK_WRITE_PATTERNS = (
    (re.compile(r"\bsed\s+-i\b"), "sed -i"),
    (re.compile(r'(?:^|[\s"\'])>>(?:[\s"\'$]|$)', re.M), ">>"),
    (re.compile(r"\bmake\b[^\n]*\bindex\b"), "make … index"),
)


def read_only_fork_agents() -> set[str]:
    """Agent names (agents/<name>.md) whose disallowedTools denies both Edit and Write."""
    out: set[str] = set()
    agents_dir = KIT / "agents"
    if not agents_dir.is_dir():
        return out
    for p in sorted(agents_dir.glob("*.md")):
        fm = fmt.parse(p.read_text(encoding="utf-8", errors="replace"))
        if not fm:
            continue
        denied = set(fmt.parse_csv(fm.get("disallowedTools", "")))
        if {"Edit", "Write"} <= denied:
            out.add(fmt.unit_name(p))
    return out


def check_forked_write_leak(rel, fm, body: str, errors: list[str]) -> None:
    """A skill whose `agent:` names a read-only fork (`read_only_fork_agents()`) must not carry a
    state-mutating shell form in its own body — see FORK_WRITE_PATTERNS above."""
    agent = fmt.unquote(fm.get("agent", ""))
    if not agent or agent not in read_only_fork_agents():
        return
    for pat, label in FORK_WRITE_PATTERNS:
        m = pat.search(body)
        if m:
            line = body.count("\n", 0, m.start()) + 1
            errors.append(f"{rel}: body line {line} uses `{label}` but `agent: {agent}` denies Edit/Write — "
                          "a read-only fork must not mutate state through Bash either; describe the write as "
                          "something the main session applies, never a step the fork runs itself")


# `.context/` writes go through the ctx MCP tools (`ctx_log`/`ctx_str_replace`/`ctx_insert`/`ctx_create`/`ctx_new`/
# `ctx_fm`) or `ctx_adapter.py ctx <verb>` from Bash — never Write/Edit/a shell redirect (#322/#324). The
# PreToolUse deny already stops a Write/Edit tool call; this check exists for what that hook cannot see: a
# Bash-run heredoc/`sed -i`/`tee`/`cat >`/`>>` reaching the file directly, or prose that never names which tool
# routes the change, so a reader (model or human) reaches for Write/Edit by habit. Exempt what the deny exempts
# (ctx_adapter.py's `_exempt()`): the index's skip dirs, a dot dir, INDEX.md/SESSION_INDEX.md, README.md at any
# level, and anything that isn't a `.md` doc.
CTX_WRITE_SKIP_DIRS = {"bin", "_templates", "sessions", "handoff", "memory", "state"}
CTX_DOC_PATH = re.compile(r"`?\.context/((?:[\w.<>-]+/)+[\w.<>-]+\.md)`?")
CTX_WRITE_VERB = re.compile(r"\b(?:[Ee]dit|[Ww]rite|[Oo]verwrite|[Rr]ewrite|[Aa]ppend(?:ed|ing)?|[Tt]ick(?:ed|ing)?)\b")
CTX_SHELL_WRITE = re.compile(r"\bsed\s+-i\b|(?:^|[\s\"'])>>(?:[\s\"'$]|$)|\bcat\s*>{1,2}|\btee\b|<<['\"]?[A-Za-z_]\w*['\"]?")
CTX_TOOL_NAMED = re.compile(r"\bctx_(?:log|str_replace|insert|create|new|fm|delete|rename|move|touch|migrate|"
                            r"maintain|resolve|validate|find|get|view|brief|doctor)\b|ctx_adapter\.py\s+ctx\b|"
                            r"\bctx\s+(?:tools?|verbs?)\b")
CTX_APPEND_LINE = re.compile(r"\bappend(?:ed|ing)?\s+(?:a\s+|one\s+)?(?:line|bullet|entry|row|checkbox)\b", re.I)


def _ctx_write_exempt(rel: str) -> bool:
    """A path a model may still touch directly, mirroring ctx_adapter.py's `_exempt()` (its store-settings
    generated/ignore globs aside — a prose scan has no adopted store to ask): not a `.md` doc, a dot dir, one of
    the index's skip dirs (`bin _templates sessions handoff memory state`), or INDEX.md/SESSION_INDEX.md/
    README.md at any level."""
    parts = rel.split("/")
    if not rel.endswith(".md") or any(p.startswith(".") for p in parts):
        return True
    return bool(set(parts[:-1]) & CTX_WRITE_SKIP_DIRS) or parts[-1] in ("INDEX.md", "SESSION_INDEX.md", "README.md")


def _prose_units(body: str) -> list[tuple[int, str]]:
    """(start line, joined text) for each top-level line of a body, its indented wrapped-continuation lines
    (a numbered/bulleted step's own sub-bullets and soft-wrapped prose) folded in — the granularity
    `ctx_write_prose_hits` reads a step's own tool mentions at: a `ctx_log` two steps down a numbered list
    must not excuse a different step's unrouted write. Fenced code is skipped, never scanned as prose."""
    out: list[tuple[int, str]] = []
    start: int | None = None
    buf: list[str] = []
    fenced = False
    for n, line in enumerate(body.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            if buf:
                out.append((start, " ".join(buf)))
            buf, start = [], None
            continue
        if fenced:
            continue
        if not line.strip():
            if buf:
                out.append((start, " ".join(buf)))
            buf, start = [], None
        elif line[:1].isspace() and buf:
            buf.append(line.strip())
        else:
            if buf:
                out.append((start, " ".join(buf)))
            buf, start = [line.strip()], n
    if buf:
        out.append((start, " ".join(buf)))
    return out


CTX_WRITE_PROXIMITY = 80  # chars either side of a flagged path a write verb/shell redirect must fall within — a
# path mentioned only in passing elsewhere in the same step (e.g. "the ledger feeds `.context/reference/x.md`",
# a read-only pipeline note) must not be flagged just because the step's OWN write target, a few sentences
# away, happens to use a write verb too.


def ctx_write_prose_hits(body: str) -> list[tuple[int, str]]:
    """(line, message) for prose that tells the model to write a `.context/` doc directly instead of naming a
    ctx tool — see the module comment above CTX_WRITE_SKIP_DIRS. A unit (one top-level step, its sub-bullets and
    wrapped continuation folded in by `_prose_units`) is exempt the moment it names a ctx tool, `ctx_adapter.py
    ctx`, or the phrase `ctx tools`/`ctx verbs` anywhere in it — a step that delegates to another skill by name
    (`session-handoff`) without saying so is not read as routed; the delegate's own prose is checked on its own
    unit. Within a unit, a write verb must fall within CTX_WRITE_PROXIMITY characters of the specific path it
    is read as acting on — a step can legitimately mention more than one `.context/` path, and only some
    of them its own write target."""
    out: list[tuple[int, str]] = []
    for start, text in _prose_units(body):
        if CTX_TOOL_NAMED.search(text):
            continue
        hit = ""
        for m in CTX_DOC_PATH.finditer(text):
            if _ctx_write_exempt(m.group(1)):
                continue
            lo, hi = max(0, m.start() - CTX_WRITE_PROXIMITY), min(len(text), m.end() + CTX_WRITE_PROXIMITY)
            window = text[lo:hi]
            if CTX_WRITE_VERB.search(window) or CTX_SHELL_WRITE.search(window):
                hit = m.group(1)
                break
        if hit:
            out.append((start, f"names `.context/{hit}` without naming a ctx tool — write it through "
                        "`ctx_str_replace`/`ctx_insert`/`ctx_log`/`ctx_fm`/`ctx_create`/`ctx_new` (or "
                        "`ctx_adapter.py ctx <verb>` from Bash), never Write/Edit/a shell redirect"))
        elif CTX_APPEND_LINE.search(text) and (
                "context doc" in text.lower()
                or any(not _ctx_write_exempt(m.group(1)) for m in CTX_DOC_PATH.finditer(text))):
            # only when the unit is about a context doc: "append a line to the PR body" is not
            out.append((start, "\"append a line\" to a context doc without naming a ctx tool"))
    return out


def check_ctx_write_prose(rel, body: str, errors: list[str]) -> None:
    for line, msg in ctx_write_prose_hits(body):
        errors.append(f"{rel}: body line {line} {msg}")


STEP = re.compile(r"^(\d+)\.\s+(.*\S)")

# The fork hand-back line is `NEEDS <system>.<kind> <name>` — never a colon (docs/env-facts.md § Environment
# facts): a colon would give a forked worker two spellings for the same signal and a main session matching on
# one form misses the other.
NEEDS_COLON = re.compile(r"\bNEEDS:")

# The capability-gate stop line docs/authoring.md § Body promises: `<skill>: not applicable here — <why>` when
# a required flag is false. A unit with metadata.requires must spell this literal somewhere in its body, so a
# user or an eval can match on it.
NOT_APPLICABLE = "not applicable here —"


def duplicated_steps(body: str) -> list[str]:
    """Top-level numbered lists that repeat a step number or a step's text (#19). A list runs until a heading or an
    unindented line that is not a step; blank and indented (continuation) lines keep it open. Fenced code is skipped."""
    out: list[str] = []
    nums: dict[str, int] = {}
    texts: dict[str, int] = {}
    fenced = False
    for n, line in enumerate(body.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = STEP.match(line)
        if m:
            num, text = m.group(1), " ".join(m.group(2).split())
            if num in nums:
                out.append(f"body line {n} repeats step `{num}.` (first on line {nums[num]})")
            elif text in texts:
                out.append(f"body line {n} repeats the text of the step on line {texts[text]}")
            nums.setdefault(num, n)
            texts.setdefault(text, n)
        elif line.startswith("#") or (line.strip() and not line[0].isspace()):
            nums, texts = {}, {}  # a heading or a paragraph ends the list
    return out


def check_body(p: Path, rel, body: str, errors: list[str], is_agent: bool = False, warn: list[str] | None = None) -> None:
    """Environment-free checks on a unit's body: length, cited own paths exist, no fact-shaped literal."""
    warn = warn if warn is not None else []
    if is_agent:
        for n, line in enumerate(body.split("\n"), 1):
            if NEEDS_COLON.search(line):
                errors.append(f"{rel}: body line {n} spells a `NEEDS:` report line with a colon — the fork "
                              "hand-back form is `NEEDS <system>.<kind> <name>`, never a colon (one spelling, "
                              "docs/env-facts.md § Environment facts)")
    n_lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
    allow_reason = BODY_LINES_ALLOW.get(fmt.unit_name(p))
    if n_lines > BODY_MAX_LINES:
        errors.append(f"{rel}: body is {n_lines} lines > {BODY_MAX_LINES} — move detail into references/ (loaded on "
                      f"demand); BODY_LINES_ALLOW only silences the {BODY_WARN_LINES}-line warning, not this cap")
    elif allow_reason is None and n_lines > BODY_WARN_LINES:
        warn.append(f"{rel}: body is {n_lines} lines > {BODY_WARN_LINES} (docs/authoring.md's target) — move detail "
                    "into references/ (loaded on demand), or name the unit in BODY_LINES_ALLOW (kit_verify.py) "
                    "with a reason")
    unit_dir = p.parent
    for n, target in cited_make_targets(body):
        if target not in make_targets():
            errors.append(f"{rel}: cites `make -C $BATON/context-db {target}` (body line {n}) but the Makefile has no such target")
    for m in OWN_PATH.finditer(body):
        cited = m.group(1)
        if cited.startswith("$BATON/"):  # the kit root on both install paths (#3) — resolve it like the clone path
            cited = ".claude/" + cited[len("$BATON/"):]
        if any(c in cited for c in "*<{$"):
            continue  # a glob or a placeholder, not a file
        if cited.startswith(".claude/"):
            inner = cited.split("/", 3)[-1] if cited.startswith(".claude/skills/") else cited.split("/", 2)[-1]
            if cited.startswith(".claude/skills/") and cited.split("/")[2] != unit_dir.name:
                continue  # another skill's file — its own check covers it
            target = unit_dir / inner
        else:
            target = unit_dir / cited
        if not target.exists() and not target.with_suffix("").exists():
            errors.append(f"{rel}: cites `{cited}` but {target.relative_to(KIT.parent) if target.is_relative_to(KIT.parent) else target} does not exist")
    for dup in duplicated_steps(body):
        errors.append(f"{rel}: {dup} — a mis-resolved merge? keep one (#19)")
    for n, line in enumerate(body.split("\n"), 1):
        for m in HARDCODED_KIT_PATH.finditer(line):
            errors.append(f"{rel}: body line {n} cites `{m.group(0)}` — the kit is not at `.claude/` on a plugin install; "
                          "write `$BATON/…` (the kit root on both paths: settings.json on a clone, the SessionStart hook "
                          "on the plugin path, #3)")
    for m in CROSS_SKILL.finditer(body):
        if m.group(1) != unit_dir.name:
            errors.append(f"{rel}: cites `{m.group(0)}` — cross-reference a skill by name (`{m.group(1)}`, `/{m.group(1)}`), never by "
                          "its path: installed paths differ per host (docs/contributing.md § Skills)")
    check_ctx_write_prose(rel, body, errors)
    for n, what, hit in leak_shapes.scan(p.read_text(encoding="utf-8", errors="replace"), rel=str(rel), allow=leak_shapes.allowed()):
        errors.append(f"{rel}:{n}: {what} `{hit}` — an environment fact never ships in a unit; read it from the env store "
                      "(`kb.py get`, declare it in metadata.facts) or use a `<placeholder>` (a dated example that must stay "
                      "verbatim: skills/kit-health/allow.txt, the one allow-list both scanners read)")


def check_unit(p: Path, rel, errors: list[str], stale: list[str], stale_days: int = 0, today: date | None = None,
               warn: list[str] | None = None, no_git: bool = False) -> int:
    """Every frontmatter check for one skill/agent file; appends problems to `errors` (and stale notes to
    `stale`, non-blocking notes to `warn`), returns the description size in bytes."""
    today = today or date.today()
    warn = warn if warn is not None else []
    desc_total = 0
    is_agent = p.parent.name == "agents"
    parts = fmt.split(p.read_text(encoding="utf-8", errors="replace"))  # one read + split, reused below
    fm = fmt.parse_lines(parts[0]) if parts else None
    for dup in (fmt.duplicate_keys(parts[0]) if parts else []):  # #19: the parser keeps the last value silently
        errors.append(f"{rel}: frontmatter repeats {dup} (block lines) — a mis-resolved merge? keep one")
    if fm is None:
        errors.append(f"{rel}: no frontmatter block")
        return 0
    body = parts[1] if parts else ""
    check_body(p, rel, body, errors, is_agent=is_agent, warn=warn)
    if not is_agent:
        check_forked_write_leak(rel, fm, body, errors)
    for line in (parts[0] if parts else []):  # YAML reads ` #` in a bare scalar as a comment: the value is cut there
        km = fmt.KEY.match(line) or fmt.SUBKEY.match(line)
        val = (km.group(2) or "").lstrip() if km else ""
        if not val or fmt.is_quoted(val):
            continue  # empty (a nested mapping) or already quoted (either style — frontmatter.is_quoted)
        if val.startswith("#") or re.search(r"\s#", val):
            kept = "" if val.startswith("#") else fmt.strip_comment(val)  # `key: #…` is null to YAML, not `#…`
            errors.append(f"{rel}: '{km.group(1)}:' contains a `#` comment — YAML cuts the value there "
                          f"(kept: {kept!r}); quote the whole value, or move the comment to its own `#` line "
                          "(the right fix for a true/false or integer key)")
            continue
        problem = fmt.plain_scalar_problem(val)
        if problem:
            errors.append(f"{rel}: '{km.group(1)}:' {problem} — a real YAML parser would refuse this file; "
                          "quote the whole value (`migrate_frontmatter.py` does it for you)")
    for line in fm.pop("_unparsed", {}).values():
        errors.append(f"{rel}: frontmatter line not understood: {line.strip()!r} — `key: value` at column 0, "
                      "or a two-space-indented `sub: value` under `metadata:`")
    allowed = SPEC_KEYS | NATIVE_KEYS | (AGENT_KEYS if is_agent else set())
    for k in fm:
        if k in RETIRED_KEYS:
            errors.append(f"{rel}: '{k}:' is retired — the kit never names an environment; gate the unit "
                          "with `metadata.requires: \"<capability>\"` instead (or nothing, if it works everywhere)")
        elif k in fmt.KIT_META_KEYS:
            errors.append(f"{rel}: '{k}:' belongs under `metadata:` as a string — run "
                          "`python3 $BATON/context-db/bin/migrate_frontmatter.py`")
        elif k not in allowed:
            errors.append(f"{rel}: top-level key '{k}' is neither an Agent Skills spec key nor an allow-listed Claude Code "
                          f"key {sorted(allowed)} — kit bookkeeping goes under `metadata:`; a new native key is added to "
                          "kit_verify.py and docs/contributing.md § Skill frontmatter with its reason")
    for k in ("name", "description"):
        if k not in fm:
            errors.append(f"{rel}: missing '{k}'")
    name = fmt.unquote(fm.get("name", ""))
    if name and name != fmt.unit_name(p):
        errors.append(f"{rel}: name '{name}' must equal the {'file stem' if is_agent else 'directory name'} '{fmt.unit_name(p)}'")
    meta = fm.get("metadata")
    if not isinstance(meta, dict):
        errors.append(f"{rel}: missing `metadata:` mapping (version, updated, reviewed — run migrate_frontmatter.py)")
        meta = {}
    for k in META_REQUIRED:
        if k not in meta:
            errors.append(f"{rel}: missing metadata.{k}")
    for k, v in meta.items():
        if not fmt.is_quoted_string(v):
            errors.append(f"{rel}: metadata.{k} must be a double-quoted string (spec: metadata is string → string), got {v!r}")
    m = {k: fmt.unquote(v) for k, v in meta.items()}
    if "version" in m and not m["version"].isdigit():
        errors.append(f"{rel}: metadata.version '{m['version']}' is not an integer")
    for k in ("updated", "reviewed"):
        if k in m:
            try:
                d = datetime.strptime(m[k], DATE).date()
                if k == "reviewed" and stale_days and (today - d).days > stale_days:
                    stale.append(f"{rel}: reviewed {m[k]} ({(today - d).days}d ago)")
                if k == "reviewed" and not no_git:
                    check_reviewed_freshness(KIT, rel, m[k], warn)
            except ValueError:
                errors.append(f"{rel}: metadata.{k} '{m[k]}' is not YYYY-MM-DD")
    desc_total += check_description(rel, fm, errors)
    check_description_shape(rel, fm, warn)
    pm = re.match(r"\(([a-z][a-z0-9-]*)\) ", fmt.unquote(fm.get("description", "")))
    if pm:
        errors.append(f"{rel}: description starts with '({pm.group(1)}) ' — drop the environment prefix; "
                      "`metadata.requires` says where the unit applies")
    if "requires" in m:
        reqs = fmt.parse_csv(m["requires"])
        if not reqs:
            errors.append(f"{rel}: metadata.requires must be a comma-separated list of capability flags")
        for r in reqs:
            if r not in SYSTEMS:
                hint = f" (renamed to '{kb.RENAMED_SYSTEMS[r]}')" if r in kb.RENAMED_SYSTEMS else ""
                errors.append(f"{rel}: requires '{r}' is not a known capability flag{hint} {sorted(SYSTEMS)}")
        compat = fmt.unquote(fm.get("compatibility", ""))
        if not compat:
            errors.append(f"{rel}: a unit with metadata.requires carries `compatibility: \"Designed for Claude Code; needs "
                          f"{', '.join(reqs)} (systems.*)\"` so a spec-only reader sees the gate (migrate_frontmatter.py adds it)")
        else:
            for r in reqs:
                if not re.search(rf"\b{re.escape(r)}\b", compat):
                    errors.append(f"{rel}: compatibility does not name the required flag '{r}'")
        if reqs and NOT_APPLICABLE not in body:
            errors.append(f"{rel}: a unit with metadata.requires has no `<skill>: not applicable here — <why>` stop "
                          "line in its body — the literal phrase 'not applicable here —' (em dash) must appear "
                          "somewhere in the text (docs/authoring.md § Body)")
    if "compatibility" in fm and len(fmt.unquote(fm["compatibility"])) > 500:
        errors.append(f"{rel}: compatibility is longer than the spec's 500 characters")
    declared: set[str] = set()
    if "facts" in m:
        entries = fmt.parse_csv(m["facts"])
        if not entries:
            errors.append(f"{rel}: metadata.facts must be a comma-separated list of `<system>.<kind>[ <name>]` / `<config.key>` entries")
        for e in entries:
            key, _, nm = e.partition(" ")
            declared.add(key)
            if not kb.FACT_KEY.match(e):
                errors.append(f"{rel}: facts entry '{e}' is not `<system>.<kind>[ <name>]` or a dotted config key")
            elif key.startswith("systems.") and key.count(".") == 1:
                # a `systems.*` capability flag is not discovered through a manifest — every store carries the
                # whole set (check_env_store) — so declaring one only has to name a real flag
                flag = key.split(".", 1)[1]
                if flag not in SYSTEMS:
                    errors.append(f"{rel}: facts entry '{e}' names unknown capability flag '{flag}' {sorted(SYSTEMS)}")
            elif kb.find_fact(key, nm.strip()) is None:
                errors.append(f"{rel}: facts entry '{e}' has no discovery manifest — add it to "
                              "context-db/discovery/<system>.json (see its README.md) so sessions know how to find it")
    audit_facts_read(rel, body, declared, errors)
    return desc_total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kit_verify.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--stale", type=int, default=0, metavar="N", help="also list units whose reviewed is older than N days")
    ap.add_argument("--no-env", action="store_true", help="environment-free: skip the env-store checks (a bare clone, a contributor's PR)")
    ap.add_argument("--no-git", action="store_true", help="skip the reviewed-vs-last-edit check (no git history to compare against)")
    ap.add_argument("--loading-table", action="store_true",
                    help="print the numbers docs/loading.md quotes (units, description bytes, bodies, body bytes) and exit")
    ap.add_argument("--write", action="store_true",
                    help="with --loading-table: rewrite a marked number in docs/loading.md, but only one the "
                         "drift check would fail on (a count that differs, or a byte total outside tolerance)")
    ap.add_argument("--force", action="store_true",
                    help="with --loading-table --write: also rewrite markers within tolerance (a deliberate "
                         "refresh, e.g. at release time)")
    ap.add_argument("units", nargs="*", type=Path, help="skill/agent files or dirs to verify (default: every unit; skips the kit-wide totals)")
    a = ap.parse_args(argv)
    if a.loading_table:
        values = loading_table_values()
        notes = write_loading_table(values, force=a.force) if a.write else {}
        for key, val in values.items():
            note = notes.get(key)
            print(f"{key}: {val}" + (f" {note}" if note else ""))
        return 0
    saved_kb_env = kb.ENV
    if a.no_env:
        kb.ENV = NO_ENV_STORE  # environment-free for the whole run: `check_unit`'s facts-declared check and the
        # final discoverable-facts count must see the kit's own manifests only, exactly like `validate_manifests_kit()` —
        # a machine's `_discovery/` overlay is never consulted under --no-env, poisoned or not
    try:
        units: list[Path] = []
        for u in a.units:
            u = u if u.is_absolute() else Path.cwd() / u
            if u.is_dir():
                cand = u / "SKILL.md"
                if not cand.is_file():
                    print(f"kit-verify: {u} is neither a skill dir (SKILL.md) nor a unit file", file=sys.stderr)
                    return 2
                u = cand
            if not u.is_file():
                print(f"kit-verify: {u}: no such file", file=sys.stderr)
                return 2
            units.append(u.resolve())
        partial = bool(units)
        if not partial:
            units = fmt.units(KIT)
        errors: list[str] = []
        stale: list[str] = []
        warn: list[str] = []
        skipped: list[str] = []
        if partial:
            skipped.append("always-on budget and description total (kit-wide; run without paths)")
        else:
            check_always_on_budget(errors)
            check_workspace_kit_paths(errors)
            check_readme_install(errors)
            check_plugin_manifest(errors)
            check_make_targets(errors)
            check_issue_refs(errors)
            check_loading_table_drift(errors)
        desc_total = 0
        desc_sizes: list[tuple[int, str]] = []
        today = date.today()
        for p in units:
            rel = p.relative_to(KIT) if p.is_relative_to(KIT) else p
            size = check_unit(p, rel, errors, stale, a.stale, today, warn, a.no_git)
            desc_total += size
            desc_sizes.append((size, str(rel)))
        errors += validate_manifests_kit()
        errors += [f"environment-template/config.json vs kb.blank_config(): {d}" for d in kb.config_key_drift()]
        if a.no_env:
            envname = ""
            skipped.append("env store (config.json keys, systems.*, renamed kinds, tracker.key_regex) — needs this machine's .context/")
        else:
            envname = check_env_store(errors)
        if not partial and desc_total > DESC_TOTAL_BYTES:
            errors.append(f"descriptions total {desc_total} bytes > {DESC_TOTAL_BYTES} always-on budget across {len(units)} units")
        elif not partial and desc_total > DESC_TOTAL_BYTES * DESC_TOTAL_WARN_RATIO:
            largest = ", ".join(f"{r} ({s} B)" for s, r in sorted(desc_sizes, reverse=True)[:3])
            warn.append(f"descriptions total {desc_total} B — past {int(DESC_TOTAL_WARN_RATIO * 100)}% of the {DESC_TOTAL_BYTES} B "
                        f"budget; largest: {largest}")
        for s_ in stale:
            print(f"  ~ stale: {s_}", file=sys.stderr)
        for s_ in warn:
            print(f"  ~ warn: {s_}", file=sys.stderr)
        for s_ in skipped:
            print(f"  ~ skipped: {s_}", file=sys.stderr)
        if errors:
            print(f"KIT VERIFY FAILED — {len(errors)} problem(s):", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        store = f"env store `{envname}` complete" if not a.no_env else "env store not checked (--no-env)"
        print(f"OK — {len(units)} skills/agents verified, {len(kb.fact_entries())} discoverable facts, {store}, "
              f"descriptions {desc_total} B" + (f", {len(stale)} stale" if stale else "") + (f", {len(warn)} warn" if warn else ""))
        return 0
    finally:
        kb.ENV = saved_kb_env


if __name__ == "__main__":
    raise SystemExit(main())
