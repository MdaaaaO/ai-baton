"""docs/env-vars.md parity: every environment variable a tracked script reads is documented (or explicitly
allowed to be undocumented), every documented variable is still read by something, every `kit_profile.py get`
/ `profile.get(...)` config key exists in the env-store template (or a discovery manifest), and the
`WORKSPACE_*` identity names stay in sync across `kit_profile.IDENTITY_KEYS`, `settings.local.example.json`
and the plugin's `userConfig`. Stdlib unittest, no model judgement. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import re
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402

DOC = KIT / "docs" / "env-vars.md"

# Directories a "tracked script" scan never enters: version control internals, worktrees checked out beside
# the kit, Python's own cache, a future node_modules, and the test/eval harness itself (it reads env vars for
# its own isolation plumbing — CONTEXT_ROOT, PATH — which are not something a kit user configures).
SKIP_DIRS = {".git", ".worktrees", "__pycache__", "node_modules", "tests", "evals", "fixtures"}

# `os.environ.get("NAME")` / `os.environ["NAME"]` / `os.getenv("NAME")` — the literal-string form; a name built
# at runtime (there is exactly one place that does this, kit_profile.identity(), and it is covered separately
# by IdentityParity below) is not something a static scan can see.
PY_ENV = re.compile(r"os\.(?:environ\.get|environ\[|getenv)\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']")
# The shell idiom for an optional-or-required env var: `${NAME:-default}`, `${NAME:=default}`, `${NAME:?msg}`,
# `${NAME:+alt}` and the colon-less forms. A bare `${NAME}` (no operator) is not this idiom — it is at least as
# often a plain reference to a variable the same script just assigned, which is not a "read" of anything.
SH_ENV = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::?[-=?+])")


def scan_env_reads(kit: Path = KIT) -> dict[str, set[str]]:
    """name -> the tracked-file paths (relative to `kit`) that read it, across every `.py`, `.sh`,
    `Makefile`/`*.mk` and `hooks.json` file the scan covers. The one helper both tests below and
    `docs/env-vars.md`'s rows are built from — never hand-read scripts to fill in a row."""
    found: dict[str, set[str]] = {}
    for p in sorted(kit.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(kit)
        if set(rel.parts) & SKIP_DIRS:
            continue
        if p.suffix == ".py":
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in PY_ENV.finditer(text):
                found.setdefault(m.group(1), set()).add(str(rel))
        elif p.suffix == ".sh" or p.name == "Makefile" or p.suffix == ".mk" or p.name == "hooks.json":
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in SH_ENV.finditer(text):
                found.setdefault(m.group(1), set()).add(str(rel))
    return found


# A platform variable — Claude Code's own, GitHub Actions', or POSIX's — is not the kit's to document.
PLATFORM_PREFIXES = ("CLAUDE_", "GITHUB_", "RUNNER_")
PLATFORM_EXACT = {"HOME", "TMPDIR", "CI", "NO_COLOR"}


def is_platform(name: str) -> bool:
    return name in PLATFORM_EXACT or name.startswith(PLATFORM_PREFIXES)


# Shell-local variables that only LOOK like an env read: each is assigned unconditionally (a command
# substitution or a literal), never from `${NAME:-…}` on its own right-hand side, before the line the scan
# above matches — so the match is the script re-reading its OWN prior assignment, not an external override.
SHELL_LOCAL_EXCLUDE = {
    "ENV_DOMAINS": "new.sh sets it from `kit_profile.py domains` output; the later ${ENV_DOMAINS:-…} is a "
                   "display fallback for that local string, never an external override",
    "PULLED": "sync.sh sets it from `git rev-parse` once the pull lands; ${PULLED:-…} formats that local value",
    "REC": "setup.sh sets it from `kit_profile.py get kit.install_mode`'s own output; ${REC:+…} formats it",
    "TRK_URL": "submit-review.sh sets it from `kit_profile.py get tracker.url_template`'s output; "
               "${TRK_URL:-…} is a display fallback for that local value",
    "SESSION_ID": "heartbeat.sh copies ${CLAUDE_CODE_SESSION_ID:-} (itself a platform variable) into this local "
                  "name once; later ${SESSION_ID:+…}/${SESSION_ID:-…} just format the copy",
    "MIGRATED_DIR": "setup.sh sets it to `$CONTEXT/state/memory-migrated`; ${MIGRATED_DIR:?} only guards the "
                    "backup prune against an empty path, never an external override",
}


DOC_ROW = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|")
DOC_HEADING = re.compile(r"^##\s+(.*)$")
VERSIONS_HEADING_PREFIX = "Third-party version pins"


def parse_doc(text: str) -> dict[str, str]:
    """name -> the `## heading` it was documented under, in file order."""
    rows: dict[str, str] = {}
    heading = ""
    for line in text.splitlines():
        hm = DOC_HEADING.match(line)
        if hm:
            heading = hm.group(1).strip()
            continue
        rm = DOC_ROW.match(line)
        if rm:
            rows[rm.group(1)] = heading
    return rows


class EnvVarDocs(unittest.TestCase):
    """The general env-var table: every group except the version-pins one (VersionsEnvParity below owns
    that group — those names are never read via os.environ/${…}, only sourced by a workflow)."""

    def setUp(self):
        self.found = scan_env_reads()
        self.documented = parse_doc(DOC.read_text(encoding="utf-8"))

    def test_every_tracked_read_is_documented_or_allowed(self):
        names = set(self.found) | set(kit_profile.IDENTITY_KEYS)
        undocumented = [n for n in sorted(names)
                         if not is_platform(n) and n not in SHELL_LOCAL_EXCLUDE and n not in self.documented]
        self.assertEqual(undocumented, [],
                          f"read by a tracked script but not in docs/env-vars.md (or the platform allowlist / "
                          f"SHELL_LOCAL_EXCLUDE): {undocumented} — read by, e.g.: "
                          f"{ {n: sorted(self.found.get(n, [])) for n in undocumented} }")

    def test_no_stale_rows(self):
        names = set(self.found) | set(kit_profile.IDENTITY_KEYS)
        stale = [n for n, heading in sorted(self.documented.items())
                 if not heading.startswith(VERSIONS_HEADING_PREFIX) and n not in names]
        self.assertEqual(stale, [], f"docs/env-vars.md documents these but no tracked script reads them any "
                                    f"more: {stale} — drop the row")

    def test_platform_and_shell_local_names_carry_no_row(self):
        # a row that duplicates the allowlist/exclude-list is dead weight the reader has to reconcile by hand.
        # The version-pins group is exempt: CLAUDE_CODE_VERSION is a versions.env pin name, not the CLAUDE_*
        # runtime variable family (coincidence of prefix, not of meaning).
        for name, heading in self.documented.items():
            if heading.startswith(VERSIONS_HEADING_PREFIX):
                continue
            self.assertFalse(is_platform(name), f"{name} is a platform variable and should carry no row")
            self.assertNotIn(name, SHELL_LOCAL_EXCLUDE, f"{name} is shell-local (see SHELL_LOCAL_EXCLUDE) and "
                                                         "should carry no row")



# ── config keys read through kit_profile.py get / profile.get(...) ──────────────────────────────────────────
CONFIG_METHOD_CALL = re.compile(r"\b(?:kit_profile|profile)\.get\(\s*[\"']([\w.]+)[\"']")
CONFIG_WRAPPER_CALL = re.compile(r"\benv_get\(\s*[\"']([\w.]+)[\"']")  # diagram-plan.py's local kit_profile.get() wrapper
CONFIG_CLI_CALL = re.compile(r"kit_profile\.py\"?\s+get\s+([\w.]+)")


def scan_config_keys(kit: Path = KIT) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for p in sorted(kit.rglob("*")):
        if not p.is_file() or (set(p.relative_to(kit).parts) & SKIP_DIRS):
            continue
        if p.suffix not in (".py", ".sh"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(kit))
        for rx in (CONFIG_METHOD_CALL, CONFIG_WRAPPER_CALL, CONFIG_CLI_CALL):
            for m in rx.finditer(text):
                found.setdefault(m.group(1), set()).add(rel)
    return found


def config_key_covered(cfg: dict, key: str, discovery_config_keys: set[str]) -> bool:
    if key in discovery_config_keys:
        return True
    cur = cfg
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def discovery_config_keys(kit: Path = KIT) -> set[str]:
    keys: set[str] = set()
    for p in (kit / "context-db" / "discovery").glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        for fact in data.get("facts", []):
            if fact.get("target") == "config":
                keys.add(fact["key"])
    return keys


class ConfigKeyParity(unittest.TestCase):
    """Every config.json key a script reads through `kit_profile.get(...)` / `kit_profile.py get <key>`
    exists in `environment-template/config.json` (nested), or is a `target: config` fact in a discovery
    manifest — so a key a script starts reading always has somewhere a new environment learns its shape."""

    def test_every_read_config_key_exists_in_the_template_or_a_manifest(self):
        template = json.loads((KIT / "environment-template" / "config.json").read_text(encoding="utf-8"))
        manifest_keys = discovery_config_keys()
        found = scan_config_keys()
        missing = [key for key in sorted(found) if not config_key_covered(template, key, manifest_keys)]
        self.assertEqual(missing, [], f"read via kit_profile.get()/`kit_profile.py get` but not in "
                                      f"environment-template/config.json or a discovery manifest's `target: "
                                      f"config` facts: {missing} — read by, e.g.: "
                                      f"{ {k: sorted(found.get(k, [])) for k in missing} }")


# ── WORKSPACE_* identity: one dict, three places that must agree ────────────────────────────────────────────
class IdentityParity(unittest.TestCase):
    def test_identity_keys_match_settings_example_and_plugin_userconfig(self):
        identity_names = set(kit_profile.IDENTITY_KEYS)
        option_keys = set(kit_profile.IDENTITY_KEYS.values())
        settings_env = json.loads((KIT / "settings.local.example.json").read_text(encoding="utf-8"))["env"]
        self.assertEqual(set(settings_env), identity_names,
                         "settings.local.example.json's `env` keys must be exactly kit_profile.IDENTITY_KEYS")
        plugin = json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(set(plugin["userConfig"]), option_keys,
                         "plugin.json's userConfig keys must be exactly kit_profile.IDENTITY_KEYS.values()")

    def test_no_script_reads_a_workspace_name_kit_profile_does_not_know(self):
        found = scan_env_reads()
        read_workspace_names = {n for n in found if n.startswith("WORKSPACE_")}
        unknown = sorted(read_workspace_names - set(kit_profile.IDENTITY_KEYS))
        self.assertEqual(unknown, [], f"a script reads a WORKSPACE_* name kit_profile.IDENTITY_KEYS does not "
                                      f"list: {unknown}")


# ── .github/versions.env: every pin defined is used, every pin used is defined ──────────────────────────────
VERSIONS_ENV = KIT / ".github" / "versions.env"
WORKFLOWS = KIT / ".github" / "workflows"
PIN_DEFINE = re.compile(r"(?m)^([A-Z][A-Z0-9_]*)=\S+$")
PIN_USE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*_VERSION)\b")


def versions_env_pins() -> set[str]:
    return set(PIN_DEFINE.findall(VERSIONS_ENV.read_text(encoding="utf-8")))


def version_pin_uses() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for p in [*sorted(WORKFLOWS.glob("*.yml")), KIT / "workspace.mk"]:
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in PIN_USE.finditer(text):
            used.setdefault(m.group(1), set()).add(p.name)
    return used


class VersionsEnvParity(unittest.TestCase):
    def test_every_defined_pin_is_used_somewhere(self):
        used = version_pin_uses()
        unused = sorted(pin for pin in versions_env_pins() if pin not in used)
        self.assertEqual(unused, [], f".github/versions.env defines these but nothing uses them: {unused}")

    def test_every_used_pin_is_defined(self):
        pins = versions_env_pins()
        undefined = sorted(name for name in version_pin_uses() if name not in pins)
        self.assertEqual(undefined, [], f"referenced as a `*_VERSION` pin but not defined in "
                                        f".github/versions.env: {undefined}")


if __name__ == "__main__":
    unittest.main()
