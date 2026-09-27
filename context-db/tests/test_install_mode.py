"""One install-mode rule (#34): `kit_profile.install_mode()` names clone | plugin | dev-checkout for every kit copy
(including a git checkout not named `.claude`), `plugin_install()` and `context_root()` follow it, the MODES table
gives the mode-dependent hints, and kit-health § 1 compares the mode with the one setup.sh recorded
(`kit.install_mode`), § 4 reads the Makefile expectation from the table and the stamp records the mode.
setup.sh's side is in setup_sh_scenarios.sh (scenario 12). Stdlib unittest. Run: make -C .claude/context-db test."""
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


def kit_copy(parent: Path, name: str, git: str = "", manifest: bool = True) -> Path:
    """A minimal kit dir: `git` = "dir" | "file" (a worktree's `.git`) | "" (none); `manifest` = plugin.json."""
    kit = parent / name
    kit.mkdir(parents=True)
    if manifest:
        (kit / ".claude-plugin").mkdir()
        (kit / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "kit", "version": "1.0.0"}), encoding="utf-8")
    if git == "dir":
        (kit / ".git").mkdir()
    elif git == "file":
        (kit / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    return kit


def load_kit_health(kit: Path, ws: Path):
    spec = importlib.util.spec_from_file_location("kit_health_install_mode", KIT / "skills" / "kit-health" / "kit-health.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.KIT, mod.CTX, mod.ROOT = kit, ws / ".context", ws
    return mod


class InstallMode(unittest.TestCase):
    def test_the_three_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cases = {
                kit_copy(t / "a", ".claude", git="dir"): "clone",
                kit_copy(t / "b", ".claude", git="", manifest=True): "clone",       # a copy without git in .claude/
                kit_copy(t / "c", "1.0.0", git=""): "plugin",                        # the plugin cache
                kit_copy(t / "d", "ai-baton", git="dir"): "dev-checkout",            # a checkout NOT named .claude
                kit_copy(t / "e", "kit_topic", git="file"): "dev-checkout",          # a `.worktrees/kit_<topic>` worktree
                kit_copy(t / "f", "loose", git="", manifest=False): "dev-checkout",  # neither git nor a manifest
            }
            for kit, want in cases.items():
                self.assertEqual(kit_profile.install_mode(kit), want, kit)
                # plugin_install follows the same rule: a dict only for the plugin mode
                self.assertEqual(kit_profile.plugin_install(kit, {"CLAUDE_CONFIG_DIR": str(t / "none")}) is not None,
                                 want == "plugin", kit)
        self.assertIn(kit_profile.install_mode(), kit_profile.MODES)

    def test_a_claude_symlink_to_a_checkout_is_a_clone(self):
        # setup.sh used the name the workspace calls the kit; a `.claude` symlink to a checkout stays a clone
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / ".claude"
            link.symlink_to(KIT, target_is_directory=True)
            p = subprocess.run([sys.executable, str(link / "context-db" / "bin" / "kit_profile.py"), "install-mode"],
                               capture_output=True, text=True, env=dict(os.environ, CONTEXT_ROOT=str(Path(tmp) / ".context")))
            self.assertEqual(p.stdout.strip(), "clone", p.stderr)

    def test_a_repointed_kit_is_named_by_its_own_path(self):
        # CI checks the kit out as `<ws>/.claude`: a caller that points KIT at another copy (a plugin cache in a test)
        # must get that copy's mode, never the running checkout's name
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "cache" / "mkt" / "kit"
            (fake / ".claude-plugin").mkdir(parents=True)
            (fake / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(kit_profile, "KIT", fake), \
                 mock.patch.object(kit_profile, "KIT_AS_CALLED", Path(tmp) / "ws" / ".claude"):
                self.assertEqual(kit_profile.install_mode(), "plugin")
                self.assertEqual(kit_profile.install_mode(fake), "plugin")

    def test_mode_table_hints(self):
        kit = Path("/somewhere/kit")
        self.assertEqual(kit_profile.mode_hint("kit_ref", "clone", kit), ".claude")
        self.assertEqual(kit_profile.mode_hint("kit_ref", "plugin", kit), str(kit))
        self.assertEqual(kit_profile.mode_hint("kit_ref", "dev-checkout", kit), str(kit))
        self.assertIn("make claude_sync", kit_profile.mode_hint("update", "clone", kit))
        self.assertIn("claude plugin update p@m", kit_profile.mode_hint("update", "plugin", kit, plugin="p", market="m"))
        self.assertIn(f"git -C {kit} pull --ff-only", kit_profile.mode_hint("update", "dev-checkout", kit))
        self.assertEqual([kit_profile.mode_hint("makefile", m, kit) for m in ("clone", "plugin", "dev-checkout")], [True, False, False])
        self.assertEqual({m for m in kit_profile.MODES if kit_profile.mode_hint("workspace_md", m, kit) == "import"}, {"clone"})

    def test_mode_to_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(kit_profile.mode_to_record("", "plugin", root), "plugin")
            self.assertEqual(kit_profile.mode_to_record("clone", "plugin", root), "plugin")          # a switch
            self.assertEqual(kit_profile.mode_to_record("clone", "dev-checkout", root), "dev-checkout")  # no clone left
            (root / ".claude" / ".git").mkdir(parents=True)
            self.assertEqual(kit_profile.mode_to_record("clone", "dev-checkout", root), "clone")     # a worktree beside it
            self.assertEqual(kit_profile.mode_to_record("plugin", "dev-checkout", root), "dev-checkout")

    def test_recorded_mode_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Path(tmp) / ".context"
            env = dict(os.environ, CONTEXT_ROOT=str(ctx))
            run = lambda *a: subprocess.run([sys.executable, *a], env=env, capture_output=True, text=True)  # noqa: E731
            run(str(BIN / "kb.py"), "init", "--blank")
            self.assertEqual(run(str(BIN / "kit_profile.py"), "get", "kit.install_mode").returncode, 1)  # blank: not recorded
            self.assertEqual(run(str(BIN / "kb.py"), "config-set", "kit.install_mode", "plugin").returncode, 0)
            self.assertEqual(run(str(BIN / "kit_profile.py"), "get", "kit.install_mode").stdout.strip(), "plugin")
            p = run(str(BIN / "kit_profile.py"), "install-mode")
            self.assertEqual((p.returncode, p.stdout.strip()), (0, kit_profile.install_mode()))
            self.assertIn(run(str(BIN / "kit_profile.py"), "install-mode", "--to-record").stdout.strip(), kit_profile.MODES)
            self.assertEqual(run(str(BIN / "kit_profile.py"), "mode-hint", "bogus").returncode, 2)
            with mock.patch.object(kit_profile, "ENV_DIR", ctx / "reference" / "env"):
                kit_profile.env_config.cache_clear()
                try:
                    self.assertEqual(kit_profile.recorded_install_mode(), "plugin")
                finally:
                    kit_profile.env_config.cache_clear()


class KitHealthInstallMode(unittest.TestCase):
    def check(self, kit: Path, ws: Path, recorded: str):
        kh = load_kit_health(kit, ws)
        r = kh.Report()
        with mock.patch.object(kh.kit_profile, "recorded_install_mode", return_value=recorded):
            kh.install_mode_check(r, kh.kit_profile.install_mode(kit))
        return kh, r

    def test_recorded_vs_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            ws = t / "ws"
            ws.mkdir()
            plugin = kit_copy(t / "cache", "1.0.0")
            kh, r = self.check(plugin, ws, "")
            self.assertEqual(r.counts[kh.WARN], 1, r.lines)
            self.assertIn("not recorded", r.findings[0][2])
            self.assertIn("setup.sh", r.findings[0][2])
            kh, r = self.check(plugin, ws, "plugin")
            self.assertEqual((r.counts[kh.OK], r.counts[kh.WARN]), (1, 0), r.lines)
            self.assertIn("`plugin` = recorded", r.lines[-1])
            # switched clone → plugin, the old clone's wiring still there: each leftover is named
            (ws / ".claude" / ".git").mkdir(parents=True)
            (ws / "CLAUDE.md").write_text("# me\n\n@.claude/WORKSPACE.md\n", encoding="utf-8")
            (ws / "Makefile").write_text("include .claude/workspace.mk\n", encoding="utf-8")
            kh, r = self.check(plugin, ws, "clone")
            self.assertEqual(r.counts[kh.WARN], 1, r.lines)
            text = r.findings[0][2]
            for part in ("runs as `plugin`", "recorded `clone`", "the `.claude/` clone", "@.claude/WORKSPACE.md",
                         "include .claude/workspace.mk", "re-run `sh $BATON/setup.sh`"):
                self.assertIn(part, text)
            # a dev checkout beside the workspace's clone is expected, not a switch
            dev = kit_copy(ws / ".worktrees", "kit_topic", git="file")
            kh, r = self.check(dev, ws, "clone")
            self.assertEqual((r.counts[kh.OK], r.counts[kh.WARN]), (1, 0), r.lines)
            self.assertIn("development checkout", r.lines[-1])

    def test_makefile_expectation_comes_from_the_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            ws = t / "ws"
            (ws / ".context" / "reference" / "env").mkdir(parents=True)
            (ws / "CLAUDE.md").write_text("# me\n", encoding="utf-8")
            for kit, want in ((kit_copy(t / "d", "ai-baton", git="dir"), "not used on a dev checkout"),
                              (kit_copy(t / "c", "1.0.0"), "not used on a plugin install")):
                kh = load_kit_health(kit, ws)
                r = kh.Report()
                kh.sec_machine(r)
                self.assertTrue(any("✅" in ln and want in ln for ln in r.lines), r.lines)
                self.assertTrue(any("✅" in ln and "SessionStart hook" in ln for ln in r.lines), r.lines)
            clone = kit_copy(t / "w", ".claude", git="dir")
            kh = load_kit_health(clone, ws)
            r = kh.Report()
            kh.sec_machine(r)
            self.assertTrue(any("⚠️" in ln and "without `include .claude/workspace.mk`" in ln for ln in r.lines), r.lines)

    def test_stamp_records_the_install_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            ws = t / "ws"
            (ws / ".context").mkdir(parents=True)
            kit = kit_copy(t / "cache", "1.0.0")
            kh = load_kit_health(kit, ws)
            r = kh.Report()
            with mock.patch.object(kh, "kit_head", return_value="a" * 40), \
                 mock.patch.object(kh, "kit_version", return_value="v1.0.0"), \
                 mock.patch.object(kh, "sh", return_value=(0, "", "")):
                p, _ = kh.stamp("env", r)
            self.assertIn("\ninstall_mode: plugin\n", p.read_text(encoding="utf-8"))
            self.assertEqual(kh.read_health("env").get("install_mode"), "plugin")


if __name__ == "__main__":
    unittest.main()
