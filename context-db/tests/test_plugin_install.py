"""A plugin install (#3): the kit sits in Claude Code's plugin cache with no git checkout and no `.claude/` in the
workspace. `kit_profile.plugin_install()` names the installed kit from plugin.json + installed_plugins.json,
`context_root()` finds the workspace from the Bash tool's cwd, the SessionStart hook re-exports CLAUDE_PROJECT_DIR,
and kit-health neither calls the kit's own repo a leak nor passes a dangling `@.claude/…` import or Makefile include.
Stdlib unittest. Run: make -C .claude/context-db test."""
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

OWNER = "octo-" + "org"
SHA = "0123456789abcdef" * 2 + "01234567"
UPDATED = "2026-09-26T21:02:14.934Z"


def fake_install(tmp: Path) -> Path:
    """`<tmp>/config/plugins/cache/<marketplace>/<plugin>/<version>` with its manifest and the registry entry."""
    plugins = tmp / "config" / "plugins"
    kit = plugins / "cache" / "mkt" / "kit" / "1.2.3"
    (kit / ".claude-plugin").mkdir(parents=True)
    (kit / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "kit", "version": "1.2.3", "repository": f"https://github.com/{OWNER}/kit"}), encoding="utf-8")
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "other@mkt": [{"installPath": str(plugins / "cache" / "mkt" / "other" / "0.1.0"), "gitCommitSha": "f" * 40}],
        "kit@mkt": [{"installPath": str(kit), "gitCommitSha": SHA, "lastUpdated": UPDATED}]}}), encoding="utf-8")
    return kit


class PluginInstall(unittest.TestCase):
    def test_names_repo_commit_and_version_from_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            self.assertEqual(kit_profile.plugin_install(kit, {}), {"repo": f"{OWNER}/kit", "commit": SHA, "version": "1.2.3", "updated": UPDATED})

    def test_a_checkout_is_not_a_plugin_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            (kit / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")  # a worktree's .git is a file
            self.assertIsNone(kit_profile.plugin_install(kit, {}))
        self.assertIsNone(kit_profile.plugin_install(Path("/nonexistent/kit"), {}))  # no manifest: not a plugin either

    def test_unregistered_install_keeps_repo_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "loose"
            (kit / ".claude-plugin").mkdir(parents=True)
            (kit / ".claude-plugin" / "plugin.json").write_text(json.dumps(
                {"version": "0.1.0", "repository": f"https://github.com/{OWNER}/kit.git"}), encoding="utf-8")
            info = kit_profile.plugin_install(kit, {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "none")})
            self.assertEqual(info, {"repo": f"{OWNER}/kit", "commit": "", "version": "0.1.0", "updated": ""})


class CacheEdits(unittest.TestCase):
    """#11: a kit file changed in the plugin cache after the install is a hand edit an update would wipe."""

    def test_files_newer_than_the_install_are_listed_runtime_files_are_not(self):
        spec = importlib.util.spec_from_file_location("kit_health_cache", KIT / "skills" / "kit-health" / "kit-health.py")
        kh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kh)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            install = kit_profile.plugin_install(kit, {})["updated"]
            t0 = kh.dt.datetime.fromisoformat(install.replace("Z", "+00:00")).timestamp()
            for rel in ("skills/x/allow.txt", "skills/x/SKILL.md", ".in_use/42", "skills/x/__pycache__/a.pyc", "sign-queue/job",
                        "skills/sign-queue/SKILL.md", "evals/a/results/r.json"):
                f = kit / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("x\n", encoding="utf-8")
                os.utime(f, (t0 + 3600, t0 + 3600))
            os.utime(kit / "skills" / "x" / "SKILL.md", (t0 + 5, t0 + 5))  # written by the unpack itself
            os.utime(kit / ".claude-plugin" / "plugin.json", (t0, t0))
            # the top-level queue is runtime; the sign-queue SKILL under skills/ is kit code (review of #16)
            self.assertEqual(kh.cache_edits(kit, install), ["skills/sign-queue/SKILL.md", "skills/x/allow.txt"])
            self.assertIsNone(kh.cache_edits(kit, ""))  # install time unknown: not "no edits"
            r = kh.Report()
            with mock.patch.object(kh, "KIT", kit):
                kh.cache_wiring(r, {"updated": ""})
            self.assertTrue(any("edit check skipped" in ln for ln in r.lines), r.lines)  # said, never silent


class ContextRoot(unittest.TestCase):
    def resolve(self, kit: Path, cwd: Path, env: dict) -> Path:
        base = {k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "CLAUDE_PROJECT_DIR")}
        saved_kit, saved_cwd = kit_profile.KIT, Path.cwd()
        try:
            kit_profile.KIT = kit
            os.chdir(cwd)
            with mock.patch.dict(os.environ, {**base, **env}, clear=True):
                return kit_profile.context_root()
        finally:
            kit_profile.KIT = saved_kit
            os.chdir(saved_cwd)

    def test_bash_cwd_finds_the_workspace_without_claude_project_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            ws = Path(tmp) / "ws"
            (ws / ".context" / "reference" / "env").mkdir(parents=True)
            (ws / ".context" / "reference" / "env" / "config.json").write_text("{}", encoding="utf-8")
            (ws / "repo" / "src").mkdir(parents=True)
            self.assertEqual(self.resolve(kit, ws / "repo" / "src", {}), ws / ".context")  # walks up from a repo dir
            self.assertEqual(self.resolve(kit, Path(tmp), {"CLAUDE_PROJECT_DIR": str(ws)}), ws / ".context")  # the hook's export
            self.assertEqual(self.resolve(kit, Path(tmp), {}), kit.parent / ".context")  # nothing found: the old fallback

    def test_a_context_dir_without_an_env_store_is_not_a_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            (Path(tmp) / "repo" / ".context").mkdir(parents=True)  # some repo's own .context/ (no env store)
            self.assertEqual(self.resolve(kit, Path(tmp) / "repo", {}), kit.parent / ".context")
            (Path(tmp) / "repo" / ".context" / "reference" / "env").mkdir(parents=True)  # an empty env dir is not a store
            self.assertEqual(self.resolve(kit, Path(tmp) / "repo", {}), kit.parent / ".context")

    def test_an_unreadable_ancestor_is_skipped_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            real_is_file = Path.is_file

            def is_file(path):
                if path.parts[-4:] == (".context", "reference", "env", "config.json"):
                    raise PermissionError(13, "denied", str(path))
                return real_is_file(path)
            with mock.patch.object(Path, "is_file", is_file):
                self.assertEqual(self.resolve(kit, Path(tmp), {}), kit.parent / ".context")

    def test_a_checkout_never_walks_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            (kit / ".git").mkdir()  # the same tree as a checkout
            ws = Path(tmp) / "ws"
            (ws / ".context" / "reference" / "env").mkdir(parents=True)
            (ws / ".context" / "reference" / "env" / "config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(self.resolve(kit, ws, {}), kit.parent / ".context")


class SessionEnvHook(unittest.TestCase):
    def test_hook_exports_the_project_dir_for_bash(self):
        cmd = json.loads((KIT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKSPACE_", "CLAUDE_PROJECT_DIR"))}
        with tempfile.TemporaryDirectory() as tmp:
            envfile = Path(tmp) / "env"
            ws = Path(tmp) / "my ws"
            r = subprocess.run(["sh", "-c", cmd], env={**base, "CLAUDE_PLUGIN_ROOT": str(KIT), "CLAUDE_ENV_FILE": str(envfile),
                                                      "CLAUDE_PROJECT_DIR": str(ws), "CONTEXT_ROOT": "/nonexistent/.context"},
                               capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            shown = subprocess.run(["sh", "-c", envfile.read_text(encoding="utf-8") + 'printf %s "$CLAUDE_PROJECT_DIR"'],
                                   capture_output=True, text=True)
            self.assertEqual(shown.stdout, str(ws))  # quoted: a path with a space survives the eval


class WorkspaceRules(unittest.TestCase):
    def test_injected_only_into_a_plugin_path_kit_workspace(self):
        body = (KIT / "WORKSPACE.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            self.assertEqual(kit_profile.workspace_rules({}, KIT), "")  # no project dir (a non-hook caller)
            self.assertEqual(kit_profile.workspace_rules({"CLAUDE_PROJECT_DIR": str(ws)}, KIT), "")  # not a kit workspace
            (ws / ".context" / "reference" / "env").mkdir(parents=True)
            self.assertEqual(kit_profile.workspace_rules({"CLAUDE_PROJECT_DIR": str(ws)}, KIT), body)
            (ws / "repo").mkdir()
            self.assertEqual(kit_profile.workspace_rules({"CLAUDE_PROJECT_DIR": str(ws / "repo")}, KIT), body)  # a repo inside it
            (ws / ".claude").mkdir()
            (ws / ".claude" / "WORKSPACE.md").write_text("clone copy\n", encoding="utf-8")
            self.assertEqual(kit_profile.workspace_rules({"CLAUDE_PROJECT_DIR": str(ws)}, KIT), "")  # a clone imports its own
            self.assertEqual(kit_profile.workspace_rules({"CLAUDE_PROJECT_DIR": str(ws / "repo")}, KIT), "")

    def test_the_hook_prints_it_to_stdout(self):
        cmd = json.loads((KIT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKSPACE_", "CLAUDE_PROJECT_DIR"))}
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / ".context" / "reference" / "env").mkdir(parents=True)
            r = subprocess.run(["sh", "-c", cmd], env={**base, "CLAUDE_PLUGIN_ROOT": str(KIT), "CLAUDE_PROJECT_DIR": str(ws),
                                                      "CLAUDE_ENV_FILE": str(Path(tmp) / "env")}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, (KIT / "WORKSPACE.md").read_text(encoding="utf-8"))  # stdout = session context
            self.assertIn("export CLAUDE_PROJECT_DIR=", (Path(tmp) / "env").read_text(encoding="utf-8"))  # never mixed into stdout
            broken = Path(tmp) / "broken-kit"  # no WORKSPACE.md in the kit: a visible line in the session, never silence
            (broken / "context-db" / "bin").mkdir(parents=True)
            for f in (KIT / "context-db" / "bin").glob("*.py"):
                (broken / "context-db" / "bin" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            r = subprocess.run(["sh", "-c", cmd], env={**base, "CLAUDE_PLUGIN_ROOT": str(broken), "CLAUDE_PROJECT_DIR": str(ws)},
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("could not load WORKSPACE.md", r.stdout)


class KitPathResolver(unittest.TestCase):
    """Finding 1 of #3: unit bodies reach the kit as `$BATON/…` — `.claude` on a clone (settings.json), the plugin root
    on a plugin install (the SessionStart hook)."""

    def test_both_paths_set_baton(self):
        self.assertEqual(json.loads((KIT / "settings.json").read_text(encoding="utf-8"))["env"]["BATON"], ".claude")
        env = kit_profile.session_env({"CLAUDE_PLUGIN_ROOT": "/cache/kit/1.0.0", "CLAUDE_PROJECT_DIR": "/ws"})
        self.assertEqual((env["BATON"], env["CLAUDE_PROJECT_DIR"]), ("/cache/kit/1.0.0", "/ws"))
        self.assertNotIn("BATON", kit_profile.session_env({}))

    def test_hardcoded_kit_paths_are_findings_workspace_paths_are_not(self):
        import kit_verify
        hits = lambda line: [m.group(0) for m in kit_verify.HARDCODED_KIT_PATH.finditer(line)]
        self.assertEqual(hits("run `python3 .claude/context-db/bin/kb.py get x`"), [".claude/context-db/bin/kb.py"])
        self.assertEqual(hits("`bash .claude/skills/pr-review/scripts/fetch-context.sh`"), [".claude/skills/pr-review/scripts/fetch-context.sh"])
        for fine in ("`$BATON/context-db/bin/kb.py`", "`~/.claude/projects/*/*.jsonl`", "`<repo>/.claude/commit-style`",
                     "`.claude/settings.local.json`", "`.claude/sign-queue/logs`", "`**/.claude/**`"):
            self.assertEqual(hits(fine), [], fine)
        self.assertEqual([t for _n, t in kit_verify.cited_make_targets("`make -C $BATON/context-db index`")], ["index"])

    def test_no_unit_or_workspace_body_hardcodes_the_kit_path(self):
        import kit_verify
        errors: list[str] = []
        kit_verify.check_workspace_kit_paths(errors)
        for p in [*sorted((KIT / "skills").rglob("*.md")), *sorted((KIT / "agents").glob("*.md"))]:
            for n, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                errors += [f"{p.relative_to(KIT)}:{n}: {m.group(0)}" for m in kit_verify.HARDCODED_KIT_PATH.finditer(line)]
        self.assertEqual(errors, [])


class KitHealthOnAPluginInstall(unittest.TestCase):
    def load(self, kit: Path, ws: Path):
        spec = importlib.util.spec_from_file_location("kit_health_plugin_test", KIT / "skills" / "kit-health" / "kit-health.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        mod.KIT, mod.CTX, mod.ROOT = kit, ws / ".context", ws
        return mod

    def test_kit_repo_head_and_version_come_from_the_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "none")}):
                kh = self.load(kit, Path(tmp) / "ws")
                self.assertEqual(kh.kit_repo(), f"{OWNER}/kit")  # its own install lines are not a leak
                self.assertIn("kit", kh.kit_dependencies())
                self.assertEqual(kh.kit_head(), SHA)
                self.assertEqual(kh.kit_version(), "v1.2.3")

    def test_dangling_workspace_wiring_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = fake_install(Path(tmp))
            ws = Path(tmp) / "ws"
            (ws / ".context").mkdir(parents=True)
            (ws / "CLAUDE.md").write_text("# me\n\n@.claude/WORKSPACE.md\n@.context/reference/environment.md\n", encoding="utf-8")
            (ws / "Makefile").write_text("include .claude/workspace.mk\n", encoding="utf-8")
            kh = self.load(kit, ws)
            r = kh.Report(); kh.sec_machine(r)
            errs = [l for l in r.lines if "❌" in l]
            self.assertTrue(any("WORKSPACE.md" in l and "remove the line" in l and "SessionStart hook" in l for l in errs), r.lines)
            self.assertTrue(any("workspace.mk" in l and "missing" in l and "remove the line" in l for l in errs), r.lines)
            self.assertFalse(any("✅" in l and ("WORKSPACE.md" in l or "workspace.mk" in l) for l in r.lines), r.lines)
            # the plugin-path wiring (#3): no import, no include, the hook injects WORKSPACE.md into a kit workspace
            (ws / ".context" / "reference" / "env").mkdir(parents=True)
            (ws / "CLAUDE.md").write_text("# me\n\n@.context/reference/environment.md\n", encoding="utf-8")
            (ws / "Makefile").write_text("fleet:\n\ttrue\n", encoding="utf-8")
            r = kh.Report(); kh.sec_machine(r)
            self.assertFalse([l for l in r.lines if "❌" in l and ("WORKSPACE.md" in l or "workspace.mk" in l)], r.lines)
            self.assertTrue(any("✅" in l and "SessionStart hook" in l for l in r.lines), r.lines)
            (ws / "CLAUDE.md").write_text("# me\n\n@.claude/WORKSPACE.md\n@.context/reference/environment.md\n", encoding="utf-8")
            (ws / "Makefile").write_text("include .claude/workspace.mk\n", encoding="utf-8")
            (ws / ".claude").mkdir()
            (ws / ".claude" / "WORKSPACE.md").write_text("rules\n", encoding="utf-8")
            (ws / ".claude" / "workspace.mk").write_text("\n", encoding="utf-8")
            r = kh.Report(); kh.sec_machine(r)
            self.assertTrue(any("✅" in l and "imports `@.claude/WORKSPACE.md`" in l for l in r.lines), r.lines)
            self.assertTrue(any("✅" in l and "includes `.claude/workspace.mk`" in l for l in r.lines), r.lines)


if __name__ == "__main__":
    unittest.main()
