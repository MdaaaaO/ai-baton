"""kit_verify --no-env — the environment-free validator (#114): passes on a bare clone with no .context/, checks a
fixture skill's body, fails the leaky fixture. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))

import kit_verify  # noqa: E402

FIX = HERE / "fixtures" / "skills"
LEAK = "C0" + "AB12CD3EF"  # the leaky fixture's Slack-shaped id, assembled so no scanner reads this file as a leak
FIELD = "customfield_" + "10020"  # a custom-field-shaped id, likewise


def run(*args: str, env_root: str = "/nonexistent/.context") -> tuple[int, str, str]:
    """kit_verify.py in a subprocess with CONTEXT_ROOT pointing nowhere — the bare-clone situation."""
    env = dict(os.environ, CONTEXT_ROOT=env_root)
    p = subprocess.run([sys.executable, str(BIN / "kit_verify.py"), *args], capture_output=True, text=True, env=env, cwd=KIT)
    return p.returncode, p.stdout, p.stderr


class NoEnv(unittest.TestCase):
    def test_bare_clone_passes_and_names_the_skipped_checks(self):
        rc, out, err = run("--no-env")
        self.assertEqual(rc, 0, err)
        self.assertIn("env store not checked (--no-env)", out)
        self.assertIn("~ skipped: env store", err)

    def test_without_no_env_a_missing_store_is_a_failure(self):
        rc, _, err = run()
        self.assertEqual(rc, 1)
        self.assertIn("no configuration: env store", err)

    def test_good_fixture_passes_with_partial_skips_named(self):
        rc, out, err = run("--no-env", str(FIX / "good-skill"))
        self.assertEqual(rc, 0, err)
        self.assertIn("1 skills/agents verified", out)
        self.assertIn("~ skipped: always-on budget", err)

    def test_leaky_fixture_fails_on_leak_missing_script_and_legacy_key(self):
        rc, _, err = run("--no-env", str(FIX / "leaky-skill" / "SKILL.md"))
        self.assertEqual(rc, 1)
        self.assertIn(f"Slack channel/DM id `{LEAK}`", err)
        self.assertIn("cites `scripts/missing.sh` but", err)
        self.assertIn("'version:' belongs under `metadata:`", err)

    def test_unknown_path_is_a_usage_error(self):
        rc, _, err = run("--no-env", str(FIX / "no-such-skill"))
        self.assertEqual(rc, 2)
        self.assertIn("no such file", err)


class PluginManifest(unittest.TestCase):
    """#118: plugin.json version == VERSION, kebab name, the marketplace entry points at the root — env-free."""

    def check(self, kit: Path) -> list[str]:
        saved = kit_verify.KIT
        kit_verify.KIT = kit
        try:
            errors: list[str] = []
            kit_verify.check_plugin_manifest(errors)
            return errors
        finally:
            kit_verify.KIT = saved

    def write(self, kit: Path, version="1.2.3", name="my-kit", market_name=None, market_source="./", kit_version="1.2.3"):
        (kit / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        # the identity options and the hook (#116) are what the real kit ships; check_identity_options has its own tests
        (kit / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": version, "userConfig": {
            k: {"type": "string", "title": "t", "description": "d"} for k in kit_verify.kit_profile.IDENTITY_KEYS.values()}}), encoding="utf-8")
        (kit / "hooks").mkdir(exist_ok=True)
        (kit / "hooks" / "hooks.json").write_bytes((KIT / "hooks" / "hooks.json").read_bytes())
        (kit / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"name": "m", "owner": {"name": "o"}, "plugins": [{"name": market_name or name, "source": market_source}]}), encoding="utf-8")
        (kit / "VERSION").write_text(kit_version + "\n", encoding="utf-8")

    def test_the_kit_agrees(self):
        self.assertEqual(self.check(KIT), [])

    def test_version_drift_bad_name_and_marketplace_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            self.write(kit)
            self.assertEqual(self.check(kit), [])
            self.write(kit, version="1.2.4")
            self.assertTrue(any("version '1.2.4' != VERSION '1.2.3'" in e for e in self.check(kit)))
            self.write(kit, name="My Kit")
            self.assertTrue(any("not kebab-case" in e for e in self.check(kit)))
            self.write(kit, market_name="other")
            self.assertTrue(any("no plugins[] entry" in e for e in self.check(kit)))
            self.write(kit, market_source="./plugins/x")
            self.assertTrue(any("no plugins[] entry" in e for e in self.check(kit)))
            (kit / ".claude-plugin" / "plugin.json").write_text("{nope", encoding="utf-8")
            self.assertTrue(any("invalid JSON" in e for e in self.check(kit)))
            (kit / ".claude-plugin" / "plugin.json").unlink()
            self.assertTrue(any("plugin.json: missing" in e for e in self.check(kit)))


class ProjectDirContextRoot(unittest.TestCase):
    def test_plugin_path_finds_the_projects_context_dir(self):
        # #118: with the kit installed elsewhere, CLAUDE_PROJECT_DIR/.context is the store's home
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / ".context").mkdir(parents=True)
            # a kit copy deep in a "plugin cache" with no sibling .context/, so only the fallback can answer
            cache_bin = Path(tmp) / "cache" / "plugins" / "kit" / "context-db" / "bin"
            cache_bin.mkdir(parents=True)
            (cache_bin / "kit_profile.py").write_bytes((BIN / "kit_profile.py").read_bytes())
            code = "import kit_profile; print(kit_profile.context_root())"
            env = {k: v for k, v in os.environ.items() if k != "CONTEXT_ROOT"}
            r = subprocess.run([sys.executable, "-c", code], cwd=cache_bin, env={**env, "CLAUDE_PROJECT_DIR": str(proj)},
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), str(proj / ".context"))  # the project dir's, not the cache's
            r = subprocess.run([sys.executable, "-c", code], cwd=cache_bin, env={**env, "CLAUDE_PROJECT_DIR": str(proj), "CONTEXT_ROOT": str(proj / "x")},
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), str(proj / "x"))  # CONTEXT_ROOT still wins
            r = subprocess.run([sys.executable, "-c", code], cwd=cache_bin, env=env, capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), str(cache_bin.parents[2] / ".context"))  # nothing set: beside the kit, as before


class BodyChecks(unittest.TestCase):
    def check(self, body: str, unit_dir: Path | None = None) -> list[str]:
        errors: list[str] = []
        p = (unit_dir or FIX / "good-skill") / "SKILL.md"
        kit_verify.check_body(p, p.relative_to(KIT), body, errors)
        return errors

    def test_own_paths_globs_and_placeholders(self):
        self.assertEqual(self.check("run `scripts/hello.sh` and `scripts/*.sh` and `scripts/<x>.sh`"), [])
        errors = self.check("run `scripts/nope.sh`")
        self.assertTrue(any("cites `scripts/nope.sh`" in e for e in errors), errors)
        self.assertEqual(self.check("see `$BATON/skills/other-skill/scripts/x.sh`"), [])  # another unit's file
        errors = self.check("run `$BATON/skills/good-skill/scripts/nope.sh`")  # the kit-root form is checked like a relative path
        self.assertTrue(any("cites `.claude/skills/good-skill/scripts/nope.sh`" in e for e in errors), errors)
        errors = self.check("see `.claude/skills/other-skill/scripts/x.sh`")  # the clone spelling: no such path on a plugin (#3)
        self.assertTrue(any("write `$BATON/…`" in e for e in errors), errors)

    def test_cross_skill_path_literal_is_rejected(self):
        # #115: cross-reference a skill by name; a `skills/<x>/SKILL.md` literal breaks on every host with another layout
        errors = self.check("read `$BATON/skills/other-skill/SKILL.md` § Rules")
        self.assertTrue(any("cross-reference a skill by name (`other-skill`" in e for e in errors), errors)
        errors = self.check("see `skills/other-skill/` for the rest")
        self.assertTrue(any("cross-reference a skill by name" in e for e in errors), errors)
        self.assertEqual(self.check("run `$BATON/skills/other-skill/scripts/x.sh` first"), [])  # a script a step runs: allowed
        self.assertEqual(self.check("this is `$BATON/skills/good-skill/SKILL.md`"), [])  # its own file
        self.assertEqual(self.check("see `~/my-skills/other/` or https://x.invalid/skills/other/"), [])  # not a kit path: left boundary
        for installed in ("`~/.claude/skills/other-skill/SKILL.md`", "`$HOME/.claude/skills/other-skill/`"):  # the installed-path forms
            self.assertTrue(any("cross-reference a skill by name" in e for e in self.check(f"read {installed}")), installed)
        self.assertEqual(self.check("the `other-skill` skill, or `/other-skill`"), [])  # by name

    def test_body_length_cap(self):
        errors = self.check("x\n" * (kit_verify.BODY_MAX_LINES + 1))
        self.assertTrue(any("lines > 500" in e for e in errors), errors)
        self.assertEqual(self.check("x\n" * kit_verify.BODY_MAX_LINES), [])

    def test_shape_scan_uses_the_shared_list_and_skip_rule(self):
        import leak_shapes
        text = f"channel {LEAK}\n  facts: \"slack.channel {LEAK}\"\n{FIELD}\nsee leak_shapes.py for {LEAK}\n"
        hits = leak_shapes.scan(text)
        self.assertEqual([(n, what) for n, what, _ in hits], [(1, "Slack channel/DM id"), (3, "tracker custom-field id"), (4, "Slack channel/DM id")])
        self.assertIn("leak_shapes.py", leak_shapes.SKIP_FILES)  # the scanners' own files are skipped per file, not per line

    def test_allow_list_is_honoured_and_a_bad_regex_is_skipped(self):
        import io
        import contextlib
        import leak_shapes
        with tempfile.TemporaryDirectory() as tmp:
            allow = Path(tmp) / "allow.txt"
            allow.write_text(f"# header\nskills/demo/SKILL.md:{LEAK}   # a dated example\n[unclosed\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                pats = leak_shapes.allowed(allow)
            self.assertEqual(len(pats), 1)
            self.assertIn("allow-list regex does not compile", err.getvalue())
            text = f"post to {LEAK}\n"
            self.assertEqual(leak_shapes.scan(text, rel="skills/demo/SKILL.md", allow=pats), [])
            self.assertEqual(len(leak_shapes.scan(text, rel="skills/other/SKILL.md", allow=pats)), 1)
            self.assertEqual(leak_shapes.allowed(Path(tmp) / "missing.txt"), ())
            self.assertIs(leak_shapes.allowed(allow), pats)  # compiled once per process and file


if __name__ == "__main__":
    unittest.main()
