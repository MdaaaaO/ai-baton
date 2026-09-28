"""Store isolation: one content-root resolver for every engine script, and a test run that can never reach a real
env store — `make test` always builds a throw-away one, the test package refuses any other, and `make kit-health`
audits exactly the store CONTEXT names. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
KIT = ENGINE.parent
BIN = ENGINE / "bin"
TMP = Path(tempfile.gettempdir()).resolve()
PROBE = "KIT_TEST_STORE_PROBE"  # set only by MakeTestTarget below: where StoreProbe reports the store it ran on
MAKE_VARS = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")  # an outer `make test T=…` must not leak into ours


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "CONTEXT", *MAKE_VARS)}
    return {**env, **extra}


def blank_store(ctx: Path, environment: str) -> Path:
    env = clean_env(CONTEXT_ROOT=str(ctx))
    for args in (["init", "--blank"], ["config-set", "environment", environment]):
        subprocess.run([sys.executable, str(BIN / "kb.py"), *args], env=env, check=True, capture_output=True)
    return ctx


def snapshot(root: Path) -> list[tuple[str, int]]:
    return sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())


class OneResolver(unittest.TestCase):
    """Every engine script takes its content root from kit_profile.context_root() — no private copy that falls back
    somewhere else when CONTEXT_ROOT is unset (a kit worktree resolves the store two levels up)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "ws"
        self.store = blank_store(ws / ".context", "ci")
        self.kit = ws / ".worktrees" / "kit_probe"            # a kit worktree: its store is NOT beside it
        shutil.copytree(ENGINE, self.kit / "context-db", ignore=shutil.ignore_patterns("tests", "__pycache__"))
        self.bin = self.kit / "context-db" / "bin"
        self.env = clean_env()
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_script_resolves_the_worktree_store_as_kit_profile_does(self):
        code = ("import json, kit_profile, gen_index, gen_sessions, session, verify; print(json.dumps({"
                "'kit_profile': str(kit_profile.context_root()), 'gen_index': gen_index.ROOT, 'verify': verify.gi.ROOT, "
                "'gen_sessions': gen_sessions.CTX, 'session': session.CTX}))")
        p = subprocess.run([sys.executable, "-c", code], cwd=self.bin, env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        roots = {k: str(Path(v).resolve()) for k, v in json.loads(p.stdout).items()}
        self.assertEqual(roots, dict.fromkeys(roots, str(self.store.resolve())))

        p = subprocess.run(["sh", str(self.bin / "new.sh")], cwd=self.bin, capture_output=True, text=True,
                           env={**self.env, "TYPE": "reference", "SLUG": "probe-doc", "DOMAIN": "reference"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((self.store / "reference" / "probe-doc.md").is_file(), p.stdout + p.stderr)

        # `--eval` is GNU make 4+ only (Apple ships 3.81); an included wrapper makefile works on both.
        wrapper = Path(self.tmp.name) / "show-context.mk"
        wrapper.write_text(f"include {self.kit / 'context-db' / 'Makefile'}\nshow-context: ; @printf '%s' \"$(CONTEXT)\"\n",
                           encoding="utf-8")
        p = subprocess.run(["make", "-s", "-C", str(self.kit / "context-db"), "-f", str(wrapper), "show-context"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(Path(p.stdout).resolve(), self.store.resolve(), p.stderr)

    def test_core_domains_live_in_kit_profile(self):
        sys.path.insert(0, str(BIN))
        try:
            import kit_profile
            import verify
        finally:
            sys.path.remove(str(BIN))
        self.assertEqual(verify.CORE_DOMAINS, set(kit_profile.CORE_DOMAINS))
        self.assertLessEqual(set(kit_profile.CORE_DOMAINS), verify.DOMAINS)


class SuiteGuard(unittest.TestCase):
    """The test package refuses a CONTEXT_ROOT that is not a throw-away store, and builds one when none is set."""

    def import_tests(self, cwd: Path = ENGINE, **env: str) -> subprocess.CompletedProcess:
        code = "import os, tests; print(os.environ['CONTEXT_ROOT'])"
        return subprocess.run([sys.executable, "-c", code], cwd=cwd, env=clean_env(**env), capture_output=True, text=True)

    def assertRefused(self, p: subprocess.CompletedProcess, why: str):
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("refusing to run", p.stderr)
        self.assertIn(why, p.stderr)

    def test_a_store_outside_the_temp_dir_is_refused_untouched(self):
        if TMP in KIT.resolve().parents:
            self.skipTest("this checkout sits under the temp dir")
        outside = KIT / f".no-store-{uuid.uuid4().hex[:8]}" / ".context"
        p = self.import_tests(CONTEXT_ROOT=str(outside))
        self.assertRefused(p, "not under the temp dir")
        self.assertFalse(outside.parent.exists())

    def test_a_store_naming_a_real_environment_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = blank_store(Path(tmp) / ".context", "live-" + uuid.uuid4().hex[:6])
            before = snapshot(live)
            self.assertRefused(self.import_tests(CONTEXT_ROOT=str(live)), "names the environment")
            self.assertEqual(snapshot(live), before)

    def test_the_store_beside_the_kit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            fake = ws / ".worktrees" / "kit_probe" / "context-db"
            (fake / "tests").mkdir(parents=True)
            shutil.copy2(ENGINE / "tests" / "__init__.py", fake / "tests" / "__init__.py")
            for live in (ws / ".context", ws / ".worktrees" / ".context"):   # a worktree's store, then a clone's
                live.mkdir(parents=True)
                self.assertRefused(self.import_tests(cwd=fake, CONTEXT_ROOT=str(live)), "beside this kit")

    def test_no_context_root_gets_a_blank_store_that_is_removed_at_exit(self):
        p = self.import_tests()
        self.assertEqual(p.returncode, 0, p.stderr)
        ctx = Path(p.stdout.strip())
        self.assertIn(TMP, ctx.resolve().parents)
        self.assertFalse(ctx.exists(), "the throw-away store outlived the run")

    def test_a_throw_away_store_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = blank_store(Path(tmp) / ".context", "ci")
            p = self.import_tests(CONTEXT_ROOT=str(ctx))
            self.assertEqual((p.returncode, Path(p.stdout.strip())), (0, ctx), p.stderr)


class StoreProbe(unittest.TestCase):
    """Runs only inside MakeTestTarget's nested `make test`: reports the store the suite was handed."""

    def test_probe(self):
        out = os.environ.get(PROBE)
        if not out:
            self.skipTest("the nested make-test probe")
        sys.path.insert(0, str(BIN))
        try:
            import kit_profile
        finally:
            sys.path.remove(str(BIN))
        Path(out).write_text(json.dumps({"root": os.environ.get("CONTEXT_ROOT", ""), "environment": kit_profile.name()}))


class MakeTestTarget(unittest.TestCase):
    def test_make_test_never_hands_the_suite_an_existing_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = "live-" + uuid.uuid4().hex[:6]
            live = blank_store(Path(tmp) / "ws" / ".context", marker)   # stands in for the machine's real store
            before = snapshot(live)
            out = Path(tmp) / "probe.json"
            p = subprocess.run(["make", "-s", "-C", str(ENGINE), "test", "T=test_store_isolation.StoreProbe.test_probe",
                                f"CONTEXT={live}"], env=clean_env(CONTEXT_ROOT=str(live), **{PROBE: str(out)}),
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            seen = json.loads(out.read_text())
            self.assertEqual(seen["environment"], "ci", seen)
            self.assertNotEqual(Path(seen["root"]).resolve(), live.resolve())
            self.assertIn(TMP, Path(seen["root"]).resolve().parents)
            self.assertFalse(Path(seen["root"]).exists(), "make test left its throw-away store behind")
            self.assertEqual(snapshot(live), before)


class MakeKitHealthTarget(unittest.TestCase):
    def test_kit_health_audits_the_store_context_names_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = blank_store(Path(tmp) / "a" / ".context", "kh-" + uuid.uuid4().hex[:6])
            decoy = blank_store(Path(tmp) / "b" / ".context", "decoy-" + uuid.uuid4().hex[:6])
            before = snapshot(target)
            report = Path(tmp) / "report.md"
            p = subprocess.run(["make", "-s", "-C", str(ENGINE), "kit-health", f"CONTEXT={target}",
                                f"KIT_HEALTH_ARGS=--quiet --report {report}"],
                               env=clean_env(CONTEXT_ROOT=str(decoy)), capture_output=True, text=True, timeout=300)
            self.assertTrue(report.is_file(), p.stdout + p.stderr)
            text = report.read_text(encoding="utf-8")
            self.assertIn(json.loads((target / "reference" / "env" / "config.json").read_text())["environment"], text)
            self.assertNotIn("decoy-", text)
            self.assertLessEqual(len(p.stdout.strip().splitlines()), 1, p.stdout)   # --quiet reached the script
            self.assertEqual(snapshot(target), before)


if __name__ == "__main__":
    unittest.main()
