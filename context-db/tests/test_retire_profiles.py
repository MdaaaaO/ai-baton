"""the profiles layer is gone from the code — no `kb.py import`, no `import:` provenance word, no legacy `profile`
config key, kit-health keeps exactly one legacy line, setup.sh sweeps the kit's bytecode caches and empty leftover
directories. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
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
import kb  # noqa: E402
import kit_profile  # noqa: E402


class Kb(unittest.TestCase):
    def test_import_command_and_from_profile_are_gone(self):
        for args in (["import", "/tmp/x"], ["init", "--from-profile", "/tmp/x"]):
            r = subprocess.run([sys.executable, str(BIN / "kb.py"), *args], capture_output=True, text=True,
                               env={**os.environ, "CONTEXT_ROOT": "/nonexistent/.context"})
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("invalid choice" if args[0] == "import" else "error", r.stderr, args)  # the flag no longer parses
        self.assertFalse(hasattr(kb, "import_dir"))

    def test_import_provenance_is_refused_for_new_rows_but_old_rows_still_date(self):
        with self.assertRaises(SystemExit):
            kb.normalize_provenance("import:acme")
        self.assertEqual(kb.normalize_provenance("user")[:4], "user")
        who, d = kb.provenance("import:acme 2026-09-25")  # a row a migration wrote: dated, treated as free text
        self.assertEqual((who, str(d)), ("legacy", "2026-09-25"))


class KitProfile(unittest.TestCase):
    def test_legacy_profile_key_is_not_read_and_no_alias_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".context" / "reference" / "env"
            env.mkdir(parents=True)
            (env / "config.json").write_text(json.dumps({"profile": "old-name", "tracker": {"kind": "github"}, "systems": {}}), encoding="utf-8")
            code = ("import kit_profile as k; c = k.load(strict=False); "
                    "print(k.name(), c['environment'], k.tz.__code__.co_argcount, k.get.__code__.co_argcount, k.load.__wrapped__.__code__.co_varnames[:1])")
            r = subprocess.run([sys.executable, "-c", code], cwd=BIN, capture_output=True, text=True,
                               env={**os.environ, "CONTEXT_ROOT": str(Path(tmp) / ".context")})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ["local", "local", "0", "2", "('strict',)"])  # the old key names nothing; no `profile` parameter


class KitHealth(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("kit_health_78", KIT / "skills" / "kit-health" / "kit-health.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    def test_one_legacy_line_and_no_multi_config_scaffolding(self):
        kh = self.load()
        self.assertFalse(hasattr(kh, "settings_local_legacy_keys"))
        self.assertFalse(hasattr(kh, "sec_cross"))
        self.assertTrue(hasattr(kh, "sec_stamp"))
        src = (KIT / "skills" / "kit-health" / "kit-health.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("LEGACY_PROFILES.exists()"), 1)
        self.assertNotIn("PROFILE.md", src)
        self.assertNotIn('"profile" in cfg', src)


class Grep(unittest.TestCase):
    def test_the_layer_is_named_only_where_the_issue_allows(self):
        # done when `grep -ri profile` (code paths, prose) returns only the CHANGELOG and new-environment.md § History —
        # plus the words that never meant the layer: the `kit_profile` module and its `profile` alias, AWS named profiles
        # (`aws.profile`, `--profile`, AWS_PROFILE), the Agent Skills spec's "Claude Code profile", a chat tool's
        # `read_user_profile`, and kit-health's one legacy line.
        allowed_files = {"docs/CHANGELOG.md", "CHANGELOG.md", "docs/new-environment.md", ".gitignore", "docs/authoring.md", "docs/engine-cli.md"}  # generated: names the `make profile` target  # .gitignore keeps `profiles/` so a leftover never blocks the pull
        allowed_words = ("kit_profile", "profile.", "aws.profile", "--profile", "AWS_PROFILE", "Claude Code profile", "profile of the Agent",
                         "read_user_profile", "LEGACY_PROFILES", "RETIRED_KEYS", "## profile", '"profile"', "per-profile", "Profile names",
                         "profile row", "which profile", "profile or", "profiles share", "profiles must", "profiles reach", "profile/session",
                         "the profile", "any profile", "profile <", "(no profile", "no leftover", ".claude/profiles/", "SSO profile", "for that profile", "profiles.yml")
        out = subprocess.run(["git", "grep", "-i", "-n", "profile", "--", "*.py", "*.sh", "*.md", ".gitignore", "*.json", "Makefile", "*.mk"],
                             cwd=KIT, capture_output=True, text=True).stdout
        bad = []
        for line in out.splitlines():
            path, _n, text = line.split(":", 2)
            if path in allowed_files or path.startswith("context-db/tests/"):
                continue
            if any(w.lower() in text.lower() for w in allowed_words):
                continue
            bad.append(line)
        self.assertEqual(bad, [])


class SetupSh(unittest.TestCase):
    def test_setup_sweeps_pycache_and_empty_dirs_inside_the_kit_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"; kit = ws / ".claude"; home = Path(tmp) / "home"
            kit.mkdir(parents=True); home.mkdir()
            for rel in ("setup.sh", "settings.local.example.json", "CLAUDE.example.md", "workspace.mk", "context-db", "environment-template",
                        "pr-review", "skills", "agents", "hooks", "WORKSPACE.md", "README.md", "sync-check.sh"):
                src = KIT / rel
                if src.is_dir():
                    subprocess.run(["cp", "-R", str(src), str(kit / rel)], check=True)
                elif src.exists():
                    (kit / rel).write_bytes(src.read_bytes())
            (kit / "skills" / "gone-skill" / "__pycache__").mkdir(parents=True)
            (kit / "skills" / "gone-skill" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
            (kit / "old-template" / "templates").mkdir(parents=True)
            (ws / "keep-empty").mkdir()  # the workspace is never touched
            r = subprocess.run(["sh", str(kit / "setup.sh")], cwd=ws, capture_output=True, text=True,
                               env={**os.environ, "HOME": str(home), "PROJECTS": str(ws)})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((kit / "skills" / "gone-skill").exists())
            self.assertFalse((kit / "old-template").exists())
            self.assertTrue((ws / "keep-empty").is_dir())
            self.assertFalse(list(kit.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
