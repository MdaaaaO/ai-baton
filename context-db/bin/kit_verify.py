#!/usr/bin/env python3
"""Verify the kit itself — every skill and agent carries the versioning frontmatter, this
machine's env store is complete.

Checks `skills/*/SKILL.md` and `agents/*.md` (relative to the kit root, ../.. from here) against the
Agent Skills spec's "Claude Code profile" (docs/contributing.md § Skill frontmatter):
  - every `make -C .claude/context-db <target>` a unit or kit doc cites is a Makefile target (#81),
  - only spec keys (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`) and the
    allow-listed Claude Code native keys at the top level (NATIVE_KEYS; agents also AGENT_KEYS) — any other
    key fails, and the kit's own `version`/`updated`/`reviewed`/`requires`/`facts` at the top level fail with
    a pointer to `migrate_frontmatter.py`,
  - `name` = the skill's directory / the agent's file stem,
  - `metadata:` holding version (integer), updated + reviewed (YYYY-MM-DD) as double-quoted strings,
  - optional metadata.requires (comma-separated known capability flags) — the only gate a unit carries —
    with a `compatibility:` line naming every flag, so a spec-only reader sees the gate too,
  - optional metadata.facts (comma-separated `<system>.<kind>[ <name>]` / `<config.key>` entries, each covered
    by a discovery manifest in context-db/discovery/ — the manifests themselves are validated too),
  - no `environments:` tag and no "(<name>) " description prefix: the kit never names an
    environment (retired 2026-09-25; which machine has which capability is its local env store),
  - description ≤ 60 whitespace-separated tokens and ≤ 400 B, all descriptions ≤ 9,500 B together —
    every description loads into every session's prefix (#70).
And the env store (`.context/reference/env/config.json`, see kb.py — one per machine, not in the
kit) for every top-level key `environment-template/config.json` has, `systems.*` covering every
flag a skill may `require` as booleans, a compiling one-group `tracker.key_regex`, and no rows left
under a renamed kind (`kb.py migrate` moves them).
  - the body: ≤ 500 lines (evolve's Tier 0 cap), every `scripts/…` / `references/…` / `.claude/skills/<name>/…`
    path it cites exists, and no generic environment-fact shape (Slack id, custom-field id, account id, ticket
    key, org host, tz literal — `leak_shapes.py`, shared with kit-health) in the file.
With --stale N also lists units whose `reviewed` is older than N days (warning, not an error).

`--no-env` is the ENVIRONMENT-FREE validator (#114): everything above except the env-store checks, which are
skipped and listed — it passes on a bare `git clone` with no `.context/` at all, so a contributor's PR (and CI,
before it builds a blank store) can run it. Positional paths verify only those units (`make -C .claude/context-db
verify-skill UNIT=skills/<name>`): the kit-wide totals (always-on budget, description total) are skipped too.
Run via `make -C .claude/context-db kit-verify`. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import re
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
AGENT_KEYS = {"tools", "color", "omitClaudeMd", "maxTurns"}  # agents only
META_REQUIRED = ("version", "updated", "reviewed")
RETIRED_KEYS = ("environments", "profiles")


def check_env_store(errors: list[str]) -> str:
    """Validate this machine's env store config against the template's key set. Returns the
    environment name ("" when there is no store)."""
    want_keys = kb.template_config_keys()
    p = kit_profile.ENV_DIR / "config.json"
    if not p.is_file():
        errors.append(f"no configuration: env store {p} missing — run `python3 .claude/context-db/bin/kb.py init --blank`")
        return ""
    rel = f"{p.parent.name}/{p.name}"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"{rel}: invalid JSON — {e}")
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
                          "run `python3 .claude/context-db/bin/kb.py migrate` (keeps the value)")
        missing = sorted(SYSTEMS - have)
        if missing:
            errors.append(f"{rel}: {', '.join('systems.' + s_ for s_ in missing)} missing (true/false) — "
                          "`python3 .claude/context-db/bin/kb.py migrate` adds them as false; set true what this machine has")
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
                          "`python3 .claude/context-db/bin/kb.py migrate` (moves them, keeps the cells)")
    for problem in kb.close_reason_problems(cfg):
        errors.append(f"{rel}: {problem} — run `python3 .claude/context-db/bin/kb.py migrate`")
    tracker = cfg.get("tracker")
    if tracker is None:
        tracker = {}
    elif not isinstance(tracker, dict):
        errors.append(f"{rel}: tracker must be an object {{kind, key_regex, …}} (see environment-template/config.json), got {tracker!r}")
        tracker = {}
    rx = tracker.get("key_regex")
    if tracker.get("kind") not in ("none", None) and not rx:
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
        kb.ENV = Path("/nonexistent-env-store")  # only the kit's own manifests
        return [e.replace(str(KIT) + "/", "") for e in kb.validate_manifests()]
    finally:
        kb.ENV = saved


# Always-on prefix budget (bytes): these files load into every session's cached prefix, so their size is
# re-billed on every turn. WORKSPACE.md was 25 KB before #69; the cap sits deliberately just above the
# trimmed size so every addition must earn its bytes. The template cap followed its #72 trim (2.0 KB).
ALWAYS_ON_BUDGET = {
    "CLAUDE.md": 600,  # the kit's own memory file: Claude Code loads .claude/CLAUDE.md into every workspace session too (#120)
    "WORKSPACE.md": 10_000,
    "environment-template/environment.md": 2_200,
}


PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"


def check_plugin_manifest(errors: list[str]) -> None:
    """The plugin manifest (#118): parses, kebab-case `name`, `version` == `VERSION` (conventional-release bumps both;
    the marketplace entry names the same plugin and points at the repo root). `claude plugin validate --strict .` is
    the authoritative schema check (make plugin-validate); this is the version discipline no external tool checks."""
    mp = KIT / PLUGIN_MANIFEST
    if not mp.is_file():
        errors.append(f"{PLUGIN_MANIFEST}: missing — the kit is a plugin (docs/packaging.md)")
        return
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        errors.append(f"{PLUGIN_MANIFEST}: invalid JSON — {e}")
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
    try:
        market = json.loads(mk.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        errors.append(f"{MARKETPLACE_MANIFEST}: invalid JSON — {e}")
        return
    entries = market.get("plugins") if isinstance(market.get("plugins"), list) else []
    if not any(isinstance(e, dict) and e.get("name") == name and e.get("source") in ("./", ".") for e in entries):
        errors.append(f"{MARKETPLACE_MANIFEST}: no plugins[] entry with name {name!r} and source \"./\" (the repo root)")
    check_identity_options(errors, man)


PLUGIN_HOOKS = "hooks/hooks.json"


def check_identity_options(errors: list[str], man: dict) -> None:
    """Identity via plugin userConfig (#116): every `WORKSPACE_*` variable `kit_profile.IDENTITY_KEYS` resolves has a
    `userConfig` entry of the mapped key (string, titled, described) and nothing else is declared there — the
    manifest and the resolver drift apart otherwise; and `hooks/hooks.json` carries the SessionStart hook that
    re-exports the options as `WORKSPACE_*` and `CLAUDE_PROJECT_DIR` for Bash (`kit_profile.py session-env`, #3), else the plugin path
    never sees the values the user typed into `/config`."""
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
        errors.append(f"{PLUGIN_HOOKS}: missing — the SessionStart hook that exports plugin options as WORKSPACE_* (#116)")
        return
    try:
        hooks = json.loads(hp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        errors.append(f"{PLUGIN_HOOKS}: invalid JSON — {e}")
        return
    cmds = [h.get("command", "") for grp in (hooks.get("hooks", {}).get("SessionStart") or []) if isinstance(grp, dict)
            for h in (grp.get("hooks") or []) if isinstance(h, dict)]
    if not any("session-env" in c and "CLAUDE_ENV_FILE" in c and "${CLAUDE_PLUGIN_ROOT}" in c for c in cmds):
        errors.append(f"{PLUGIN_HOOKS}: no SessionStart hook runs `${{CLAUDE_PLUGIN_ROOT}}/…/kit_profile.py session-env` into $CLAUDE_ENV_FILE")


MAKE_TARGET = re.compile(r"make(?:\s+-s)?\s+-C\s+\.claude/context-db(?:\s+-s)?\s+([a-z][a-z_-]*)\b")
MAKEFILE = KIT / "context-db" / "Makefile"
DOC_FILES = ("README.md", "CONTRIBUTING.md", "WORKSPACE.md", "docs")


def make_targets() -> set[str]:
    """Targets the engine Makefile defines (`name:` at the start of a line)."""
    if not MAKEFILE.is_file():
        return set()
    return {m.group(1) for m in re.finditer(r"^([a-z][a-z_-]*):", MAKEFILE.read_text(encoding="utf-8"), re.M)}


def cited_make_targets(text: str) -> list[tuple[int, str]]:
    """(line, target) for every `make -C .claude/context-db <target>` a text cites."""
    return [(n, m.group(1)) for n, line in enumerate(text.split("\n"), 1) for m in MAKE_TARGET.finditer(line)]


def check_make_targets(errors: list[str], files=None) -> None:
    """A doc or unit that cites `make -C .claude/context-db <target>` names a target the Makefile has (#81 E14):
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
                errors.append(f"{rel}:{n}: cites `make -C .claude/context-db {target}` but the Makefile has no such target "
                              f"(targets: {', '.join(sorted(have))})")


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


def check_description(rel, fm, errors: list[str]) -> int:
    d = fmt.unquote(fm.get("description", ""))
    words, size = len(d.split()), len(d.encode())
    if words > DESC_MAX_WORDS:
        errors.append(f"{rel}: description is {words} words (whitespace tokens) > {DESC_MAX_WORDS} — it loads into every session; "
                      "keep the trigger, the return and the one thing it never does, drop the procedure")
    if size > DESC_MAX_BYTES:
        errors.append(f"{rel}: description is {size} bytes > {DESC_MAX_BYTES}")
    return size


BODY_MAX_LINES = 500  # evolve Tier 0's cap on a skill body
# a backticked path a unit cites inside its own directory: `scripts/x.sh …`, `references/y.md`, `.claude/skills/<name>/scripts/x`
# `skills/<x>/SKILL.md` or a bare `skills/<x>/` in a body: another skill referenced by PATH. The contract is by name
# (docs/contributing.md § Skills) — installed paths differ per host and a plugin root moves on every update. A script or
# reference file of another skill that a step runs (`skills/<x>/scripts/…`) is the one allowed path form.
CROSS_SKILL = re.compile(r"(?<![\w/-])(?:~/|\$HOME/|\$\{HOME\}/)?(?:\.claude/)?skills/([\w-]+)/(?:SKILL\.md|README\.md)?(?=[`\s)'\"]|$)")
OWN_PATH = re.compile(r"`(?:[\w.-]+\s+)?((?:\.claude/(?:skills/[\w-]+|agents)/)?(?:scripts|references|reference)/[\w./-]+)")


def check_body(p: Path, rel, body: str, errors: list[str]) -> None:
    """Environment-free checks on a unit's body: length, cited own paths exist, no fact-shaped literal."""
    n_lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
    if n_lines > BODY_MAX_LINES:
        errors.append(f"{rel}: body is {n_lines} lines > {BODY_MAX_LINES} — move detail into references/ (loaded on demand)")
    unit_dir = p.parent
    for n, target in cited_make_targets(body):
        if target not in make_targets():
            errors.append(f"{rel}: cites `make -C .claude/context-db {target}` (body line {n}) but the Makefile has no such target")
    for m in OWN_PATH.finditer(body):
        cited = m.group(1)
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
    for m in CROSS_SKILL.finditer(body):
        if m.group(1) != unit_dir.name:
            errors.append(f"{rel}: cites `{m.group(0)}` — cross-reference a skill by name (`{m.group(1)}`, `/{m.group(1)}`), never by "
                          "its path: installed paths differ per host (docs/contributing.md § Skills)")
    for n, what, hit in leak_shapes.scan(p.read_text(encoding="utf-8", errors="replace"), rel=str(rel), allow=leak_shapes.allowed()):
        errors.append(f"{rel}:{n}: {what} `{hit}` — an environment fact never ships in a unit; read it from the env store "
                      "(`kb.py get`, declare it in metadata.facts) or use a `<placeholder>` (a dated example that must stay "
                      "verbatim: skills/kit-health/allow.txt, the one allow-list both scanners read)")


def check_unit(p: Path, rel, errors: list[str], stale: list[str], stale_days: int = 0, today: date | None = None) -> int:
    """Every frontmatter check for one skill/agent file; appends problems to `errors` (and stale notes to
    `stale`), returns the description size in bytes."""
    today = today or date.today()
    desc_total = 0
    is_agent = p.parent.name == "agents"
    fm = fmt.load(p)
    if fm is None:
        errors.append(f"{rel}: no frontmatter block")
        return 0
    parts = fmt.split(p.read_text(encoding="utf-8", errors="replace"))
    check_body(p, rel, parts[1] if parts else "", errors)
    for line in (parts[0] if parts else []):  # YAML reads ` #` in a bare scalar as a comment: the value is cut there
        km = fmt.KEY.match(line) or fmt.SUBKEY.match(line)
        val = (km.group(2) or "").lstrip() if km else ""
        if val and val[0] not in "\"'" and (val.startswith("#") or re.search(r"\s#", val)):
            kept = "" if val.startswith("#") else fmt.strip_comment(val)  # `key: #…` is null to YAML, not `#…`
            errors.append(f"{rel}: '{km.group(1)}:' contains a `#` comment — YAML cuts the value there "
                          f"(kept: {kept!r}); quote the whole value, or move the comment to its own `#` line "
                          "(the right fix for a true/false or integer key)")
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
                          "`python3 .claude/context-db/bin/migrate_frontmatter.py`")
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
            except ValueError:
                errors.append(f"{rel}: metadata.{k} '{m[k]}' is not YYYY-MM-DD")
    desc_total += check_description(rel, fm, errors)
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
    if "compatibility" in fm and len(fmt.unquote(fm["compatibility"])) > 500:
        errors.append(f"{rel}: compatibility is longer than the spec's 500 characters")
    if "facts" in m:
        entries = fmt.parse_csv(m["facts"])
        if not entries:
            errors.append(f"{rel}: metadata.facts must be a comma-separated list of `<system>.<kind>[ <name>]` / `<config.key>` entries")
        for e in entries:
            key, _, nm = e.partition(" ")
            if not kb.FACT_KEY.match(e):
                errors.append(f"{rel}: facts entry '{e}' is not `<system>.<kind>[ <name>]` or a dotted config key")
            elif kb.find_fact(key, nm.strip()) is None:
                errors.append(f"{rel}: facts entry '{e}' has no discovery manifest — add it to "
                              "context-db/discovery/<system>.json (see its README.md) so sessions know how to find it")
    return desc_total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kit_verify.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--stale", type=int, default=0, metavar="N", help="also list units whose reviewed is older than N days")
    ap.add_argument("--no-env", action="store_true", help="environment-free: skip the env-store checks (a bare clone, a contributor's PR)")
    ap.add_argument("units", nargs="*", type=Path, help="skill/agent files or dirs to verify (default: every unit; skips the kit-wide totals)")
    a = ap.parse_args(argv)
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
    skipped: list[str] = []
    if partial:
        skipped.append("always-on budget and description total (kit-wide; run without paths)")
    else:
        check_always_on_budget(errors)
        check_plugin_manifest(errors)
        check_make_targets(errors)
    desc_total = 0
    today = date.today()
    for p in units:
        rel = p.relative_to(KIT) if p.is_relative_to(KIT) else p
        desc_total += check_unit(p, rel, errors, stale, a.stale, today)
    errors += validate_manifests_kit()
    errors += [f"environment-template/config.json vs kb.blank_config(): {d}" for d in kb.config_key_drift()]
    if a.no_env:
        envname = ""
        skipped.append("env store (config.json keys, systems.*, renamed kinds, tracker.key_regex) — needs this machine's .context/")
    else:
        envname = check_env_store(errors)
    for s_ in stale:
        print(f"  ~ stale: {s_}", file=sys.stderr)
    for s_ in skipped:
        print(f"  ~ skipped: {s_}", file=sys.stderr)
    if not partial and desc_total > DESC_TOTAL_BYTES:
        errors.append(f"descriptions total {desc_total} bytes > {DESC_TOTAL_BYTES} always-on budget across {len(units)} units")
    if errors:
        print(f"KIT VERIFY FAILED — {len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    store = f"env store `{envname}` complete" if not a.no_env else "env store not checked (--no-env)"
    print(f"OK — {len(units)} skills/agents verified, {len(kb.fact_entries())} discoverable facts, {store}, "
          f"descriptions {desc_total} B" + (f", {len(stale)} stale" if stale else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
