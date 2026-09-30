"""Identity via plugin userConfig: `kit_profile.identity()` prefers `CLAUDE_PLUGIN_OPTION_<KEY>` over the
`WORKSPACE_*` variable settings.local.json sets, `identity-env` re-exports the options (and never the file), the
manifest declares exactly the resolver's keys, the SessionStart hook writes the exports into $CLAUDE_ENV_FILE, and
kit-health matches/labels identity from either source without printing it. Fact-shaped literals are assembled at
run time. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402
import kit_verify  # noqa: E402

LOGIN = "octo-" + "tester"
NAME = "Octo " + "Tester"
TOKYO = "Asia/" + "Tokyo"
BERLIN = "Europe/" + "Berlin"
DM = "D0" + "1ABCDEF"

# session-env --update also prints the "resolved profile" block to stdout — never into the env file;
# with CONTEXT_ROOT pointed at a store that does not exist, every value is a placeholder.
NO_STORE_PROFILE_BLOCK = (
    "resolved profile (kit_profile.py get):\n"
    "  tracker: kind=<unset> key_regex=<unset> url_template=<unset>\n"
    "  github.review_bot=<unset>\n"
    "  systems on: <none>\n"
    "  footer: none yet — `session-register` records the name; then `kit_profile.py footer` (this block shows it from the next session start)\n"
)


class Resolver(unittest.TestCase):
    def test_option_wins_then_variable_then_empty(self):
        env = {"CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN": LOGIN, "WORKSPACE_GITHUB_LOGIN": "stale", "WORKSPACE_USER": NAME}
        self.assertEqual(kit_profile.identity("WORKSPACE_GITHUB_LOGIN", env), LOGIN)
        self.assertEqual(kit_profile.identity("WORKSPACE_USER", env), NAME)  # no option → the variable
        self.assertEqual(kit_profile.identity("WORKSPACE_TZ", env), "")
        self.assertEqual(kit_profile.identity("WORKSPACE_TZ", {"CLAUDE_PLUGIN_OPTION_TZ": "  "}), "")  # blank option = unset
        self.assertEqual(kit_profile.identity("SOMETHING_ELSE", {"SOMETHING_ELSE": "x"}), "x")  # unknown: env only
        self.assertEqual(kit_profile.identity_source("WORKSPACE_GITHUB_LOGIN", env), "option")
        self.assertEqual(kit_profile.identity_source("WORKSPACE_USER", env), "env")
        self.assertEqual(kit_profile.identity_source("WORKSPACE_TZ", env), "")

    def test_every_identity_key_has_an_option_variable(self):
        for var, key in kit_profile.IDENTITY_KEYS.items():
            self.assertEqual(kit_profile.option_var(var), "CLAUDE_PLUGIN_OPTION_" + key.upper())
        self.assertEqual(set(kit_profile.IDENTITY_KEYS), {"WORKSPACE_USER", "WORKSPACE_GITHUB_LOGIN", "WORKSPACE_TZ",
                                                          "WORKSPACE_SLACK_SELF_DM", "WORKSPACE_SLACK_LATTICE_DM"})

    def test_identity_env_exports_options_only(self):
        env = {"CLAUDE_PLUGIN_OPTION_TZ": TOKYO, "WORKSPACE_GITHUB_LOGIN": LOGIN, "CLAUDE_PLUGIN_OPTION_SLACK_SELF_DM": DM + " x"}
        self.assertEqual(kit_profile.identity_env(env), {"WORKSPACE_TZ": TOKYO, "WORKSPACE_SLACK_SELF_DM": DM + " x"})
        self.assertEqual(kit_profile.identity_env({"WORKSPACE_TZ": TOKYO}), {})  # the file's value is never re-exported

    def test_tz_reads_the_option_first(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_TZ": TOKYO, "WORKSPACE_TZ": BERLIN}):
            self.assertEqual(kit_profile.tz(), TOKYO)
        with mock.patch.dict(os.environ, {"WORKSPACE_TZ": BERLIN}, clear=False):
            os.environ.pop("CLAUDE_PLUGIN_OPTION_TZ", None)
            self.assertEqual(kit_profile.tz(), BERLIN)


class Cli(unittest.TestCase):
    def run_cli(self, *args: str, **env: str) -> str:
        base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKSPACE_"))}
        r = subprocess.run([sys.executable, str(BIN / "kit_profile.py"), *args], env={**base, **env, "CONTEXT_ROOT": "/nonexistent/.context"},
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_identity_env_is_eval_able_shell_and_quiet_without_options(self):
        self.assertEqual(self.run_cli("identity-env", WORKSPACE_GITHUB_LOGIN=LOGIN), "")  # settings.local.json is never echoed
        out = self.run_cli("identity-env", CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN=LOGIN, CLAUDE_PLUGIN_OPTION_USER_NAME=NAME)
        self.assertEqual(sorted(out.splitlines()), [f"export WORKSPACE_GITHUB_LOGIN={LOGIN}", f"export WORKSPACE_USER='{NAME}'"])
        shown = subprocess.run(["sh", "-c", out + 'printf "%s|%s" "$WORKSPACE_USER" "$WORKSPACE_GITHUB_LOGIN"'], capture_output=True, text=True)
        self.assertEqual(shown.stdout, f"{NAME}|{LOGIN}")
        self.assertEqual(self.run_cli("identity-source", "WORKSPACE_TZ", CLAUDE_PLUGIN_OPTION_TZ=TOKYO).strip(), "option")
        self.assertEqual(self.run_cli("zone", CLAUDE_PLUGIN_OPTION_TZ=TOKYO, WORKSPACE_TZ=BERLIN).strip(), TOKYO)


class ManifestAndHook(unittest.TestCase):
    def check(self, man: dict, hooks: dict | None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            if hooks is not None:
                (kit / "hooks").mkdir()
                (kit / "hooks" / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
            saved = kit_verify.KIT
            kit_verify.KIT = kit
            try:
                errors: list[str] = []
                kit_verify.check_identity_options(errors, man)
                return errors
            finally:
                kit_verify.KIT = saved

    def good_manifest(self) -> dict:
        return {"userConfig": {k: {"type": "string", "title": "t", "description": "d"} for k in kit_profile.IDENTITY_KEYS.values()}}

    def test_the_kit_agrees_and_the_hook_runs_end_to_end(self):
        errors: list[str] = []
        kit_verify.check_identity_options(errors, json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")))
        self.assertEqual(errors, [])
        man = json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        for var, key in kit_profile.IDENTITY_KEYS.items():
            self.assertIn(var, man["userConfig"][key]["description"])  # the dialog names the variable it carries
        for key in ("slack_self_dm", "slack_lattice_dm"):
            self.assertTrue(man["userConfig"][key].get("sensitive"))  # chat ids never sit in a JSON settings file
        # the SessionStart hook: options in → `export WORKSPACE_*` appended to $CLAUDE_ENV_FILE, exit 0 either way
        cmd = json.loads((KIT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory() as tmp:
            envfile = Path(tmp) / "env"
            scratch = Path(tmp) / "scratch"
            base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKSPACE_"))}
            r = subprocess.run(["sh", "-c", cmd], env={**base, "CLAUDE_PLUGIN_ROOT": str(KIT), "CLAUDE_ENV_FILE": str(envfile),
                                                      "CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN": LOGIN, "CONTEXT_ROOT": "/nonexistent/.context",
                                                      "KIT_SCRATCH": str(scratch)},
                               capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, NO_STORE_PROFILE_BLOCK))
            self.assertEqual(envfile.read_text(encoding="utf-8"),
                              f"# ai-baton session-env begin\nexport WORKSPACE_GITHUB_LOGIN={LOGIN}\nexport BATON={KIT}\n"
                              "# ai-baton session-env end\n")
            # a second SessionStart on the same env file replaces the block (not skipped: a persisted
            # env file must still pick up a later CLAUDE_PROJECT_DIR / option change) and still ends
            # with exactly one block
            r = subprocess.run(["sh", "-c", cmd], env={**base, "CLAUDE_PLUGIN_ROOT": str(KIT), "CLAUDE_ENV_FILE": str(envfile),
                                                      "CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN": LOGIN, "CONTEXT_ROOT": "/nonexistent/.context",
                                                      "KIT_SCRATCH": str(scratch)},
                               capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, NO_STORE_PROFILE_BLOCK))
            self.assertEqual(envfile.read_text(encoding="utf-8"),
                              f"# ai-baton session-env begin\nexport WORKSPACE_GITHUB_LOGIN={LOGIN}\nexport BATON={KIT}\n"
                              "# ai-baton session-env end\n")
            r = subprocess.run(["sh", "-c", cmd], env={**base, "CLAUDE_PLUGIN_ROOT": str(KIT), "CONTEXT_ROOT": "/nonexistent/.context",
                                                      "KIT_SCRATCH": str(scratch)},
                               capture_output=True, text=True)  # no env file (a non-SessionStart caller): a quiet no-op
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_drift_is_a_finding(self):
        hooks = json.loads((KIT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(self.check(self.good_manifest(), hooks), [])
        man = self.good_manifest(); del man["userConfig"]["tz"]
        self.assertTrue(any("lacks `tz`" in e and "WORKSPACE_TZ" in e for e in self.check(man, hooks)))
        man = self.good_manifest(); man["userConfig"]["jira_site"] = {"type": "string", "title": "t", "description": "d"}
        self.assertTrue(any("jira_site" in e and "env store" in e for e in self.check(man, hooks)))
        man = self.good_manifest(); man["userConfig"]["tz"]["type"] = "boolean"
        self.assertTrue(any("userConfig.tz needs" in e for e in self.check(man, hooks)))
        self.assertTrue(any("hooks/hooks.json: missing" in e for e in self.check(self.good_manifest(), None)))
        self.assertTrue(any("no SessionStart hook" in e for e in self.check(self.good_manifest(), {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}]}})))


class KitHealth(unittest.TestCase):
    def load(self, kit: Path):
        spec = importlib.util.spec_from_file_location("kit_health_identity_test", KIT / "skills" / "kit-health" / "kit-health.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        mod.KIT = kit
        return mod

    def test_identity_from_options_and_file_is_matched_labelled_never_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            (kit / "settings.local.json").write_text(json.dumps({"env": {"WORKSPACE_USER": NAME, "WORKSPACE_GITHUB_LOGIN": "stale"}}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN": LOGIN}):
                for k in list(os.environ):
                    if k.startswith("WORKSPACE_"):
                        os.environ.pop(k)
                kh = self.load(kit)
                src = kh.identity_sources()
                self.assertEqual((src["WORKSPACE_GITHUB_LOGIN"], src["WORKSPACE_USER"], src["WORKSPACE_TZ"]), ("option", "settings.local.json", ""))
                self.assertTrue(kh.identity_set("WORKSPACE_USER") and not kh.identity_set("WORKSPACE_SLACK_LATTICE_DM"))
                pats = kh.identity_values()
                labels = {label for _p, label in pats}
                self.assertIn("identity (WORKSPACE_GITHUB_LOGIN, identity value)", labels)
                self.assertIn("identity (WORKSPACE_USER, full name, identity value)", labels)
                self.assertIn("identity (WORKSPACE_USER, first.last, identity value)", labels)
                for _p, label in pats:
                    self.assertNotIn(LOGIN, label); self.assertNotIn(NAME, label)
                self.assertTrue(any(p.search(f"by {LOGIN} today") for p, _l in pats))  # the option value is scanned
                self.assertFalse(any(p.search("by stale today") for p, _l in pats))  # the overridden file value is not
                self.assertTrue(any(p.search("octo.tester wrote") for p, _l in pats))

    def test_hook_exported_variables_count_as_identity_without_the_file(self):
        # the real plugin path: kit-health runs from Bash, which sees the hook's `export WORKSPACE_*` and never the
        # CLAUDE_PLUGIN_OPTION_* variables; the kit sits in a plugin cache with no settings.local.json beside it
        with tempfile.TemporaryDirectory() as tmp:
            cache_kit = Path(tmp) / "cache" / "kit"; cache_kit.mkdir(parents=True)
            base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKSPACE_"))}
            with mock.patch.dict(os.environ, {**base, "WORKSPACE_GITHUB_LOGIN": LOGIN}, clear=True):
                kh = self.load(cache_kit)
                kh.CTX = Path(tmp) / "ws" / ".context"; kh.ROOT = kh.CTX.parent
                src = kh.identity_sources()
                self.assertEqual((src["WORKSPACE_GITHUB_LOGIN"], src["WORKSPACE_USER"]), ("environment", ""))
                self.assertEqual(kh.settings_local_path(), Path(tmp) / "ws" / ".claude" / "settings.local.json")  # where setup.sh seeds it
                self.assertTrue(any(p.search(f"by {LOGIN}") for p, _l in kh.identity_values()))  # scanned like a file value
                r = kh.Report(); kh.identity_wiring(r)
                text = "\n".join(r.lines)
                self.assertNotIn("ERR", " ".join(l for l in r.lines if "settings.local.json" in l))
                self.assertIn("identity from the environment", text)
                # the project's .claude/settings.local.json (clone-style seed by setup.sh on the plugin path) is read too
                (Path(tmp) / "ws" / ".claude").mkdir(parents=True)
                (Path(tmp) / "ws" / ".claude" / "settings.local.json").write_text(json.dumps({"env": {"WORKSPACE_USER": NAME}}), encoding="utf-8")
                self.assertEqual(kh.identity_sources()["WORKSPACE_USER"], "settings.local.json")
            with mock.patch.dict(os.environ, base, clear=True):  # nothing anywhere: the one ERR, naming both remedies
                kh = self.load(Path(tmp) / "cache" / "kit2"); (Path(tmp) / "cache" / "kit2").mkdir()
                kh.CTX = Path(tmp) / "empty" / ".context"; kh.ROOT = kh.CTX.parent
                r = kh.Report(); kh.identity_wiring(r)
                self.assertTrue(any("no identity from any source" in l and "/plugin configure" in l for l in r.lines), r.lines)

    def test_machine_section_accepts_options_without_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN": LOGIN, "CLAUDE_PLUGIN_OPTION_USER_NAME": NAME,
                                              "CLAUDE_PLUGIN_OPTION_TZ": TOKYO}):
                kh = self.load(kit)
                src = kh.identity_sources()
                self.assertEqual({k for k, v in src.items() if v == "option"}, {"WORKSPACE_GITHUB_LOGIN", "WORKSPACE_USER", "WORKSPACE_TZ"})
            for k in ("CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN", "CLAUDE_PLUGIN_OPTION_USER_NAME", "CLAUDE_PLUGIN_OPTION_TZ"):
                os.environ.pop(k, None)
            kh = self.load(kit)
            self.assertEqual(set(kh.identity_sources().values()) - {""}, set() if not any(os.environ.get(v) for v in kit_profile.IDENTITY_KEYS) else {"settings.local.json"})

    def test_an_unknown_zone_is_a_warning_naming_its_source_not_its_value(self):
        # #22: an abbreviation typed into /config rendered everything in UTC behind a GREEN verdict
        for value, warned in (("nowhere-zone", True), (TOKYO, False)):
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_TZ": value}):
                kh = self.load(Path(tempfile.gettempdir()))
                r = kh.Report(); kh.zone_warning(r, kh.identity_sources())
                hits = [l for l in r.lines if "not an IANA zone" in l]
                self.assertEqual(bool(hits), warned, r.lines)
                if hits:
                    self.assertIn("plugin option `tz`", hits[0])
                    self.assertNotIn(value, hits[0])  # the value itself is never printed


if __name__ == "__main__":
    unittest.main()
