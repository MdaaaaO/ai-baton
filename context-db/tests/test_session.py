"""session.py / kit_profile without an env store, with a broken one, and the registry doc round trip. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"


def run(script: str, *args: str, root: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
    e.update({"CONTEXT_ROOT": str(root), **(env or {})})
    return subprocess.run([sys.executable, str(BIN / script), *args], env=e, cwd=BIN, capture_output=True, text=True)


class NoStore(unittest.TestCase):
    """the session scripts and kit_profile degrade to defaults with ONE warning when no env store exists."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"   # never created: the state env-init starts from

    def tearDown(self):
        self.tmp.cleanup()

    def test_kit_profile_defaults_and_one_warning(self):
        r = run("kit_profile.py", "tz", root=self.root)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "UTC"), r.stderr)
        self.assertEqual(r.stderr.count("no env store"), 1)
        r = run("kit_profile.py", "name", root=self.root)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "local"))
        r = run("kit_profile.py", "get", "tracker.key_regex", root=self.root)
        self.assertEqual(r.returncode, 1)  # unset → exit 1 as before, no traceback
        self.assertNotIn("Traceback", r.stderr)

    def test_session_register_touch_end_work_without_a_store(self):
        r = run("session.py", "register", "--name", "t1", "--no-stats", "--epic", "E-1", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.count("no env store"), 1)
        self.assertTrue((self.root / "sessions" / "t1.md").is_file())
        self.assertTrue((self.root / "SESSION_INDEX.md").is_file())
        r = run("session.py", "touch", "--name", "t1", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("session.py", "end", "--name", "t1", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("status: ended", (self.root / "sessions" / "t1.md").read_text(encoding="utf-8"))
        r = run("session_stats.py", "--session-id", "none", root=self.root)
        self.assertEqual(r.returncode, 3)  # no transcript = no stats, never an error
        self.assertNotIn("Traceback", r.stderr)


class BrokenStore(unittest.TestCase):
    def test_invalid_config_json_is_a_one_line_error(self):
        # invalid config.json was a raw traceback in every importer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".context"
            (root / "reference" / "env").mkdir(parents=True)
            (root / "reference" / "env" / "config.json").write_text("{not json", encoding="utf-8")
            for script, args in (("kit_profile.py", ["name"]), ("session.py", ["register", "--name", "x", "--no-stats"]),
                                 ("kb.py", ["config"])):
                r = run(script, *args, root=root)
                self.assertEqual(r.returncode, 1, (script, r.stderr))
                self.assertNotIn("Traceback", r.stderr, script)
                self.assertIn("config.json: invalid JSON", r.stderr, script)


class RegistryDoc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        r = run("kb.py", "init", "--blank", root=self.root)
        assert r.returncode == 0, r.stderr

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_frontmatter_keys_survive_a_touch(self):
        # write_doc emitted only FIELDS, so a key from a newer kit or a hand-added note vanished on the next touch
        r = run("session.py", "register", "--name", "t2", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.root / "sessions" / "t2.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("updated:", "owner_note: keep me\nupdated:", 1), encoding="utf-8")
        r = run("session.py", "touch", "--name", "t2", "--no-stats", "--working", "x", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text(encoding="utf-8")
        self.assertIn("owner_note: keep me", text)
        self.assertIn("working_on: x", text)
        self.assertEqual(text.count("session: t2"), 1)

    def test_ledger_row_tolerates_a_partial_stats_dict(self):
        # session.py computes CTX (and so the ledger path) from CONTEXT_ROOT at exec time — kit_profile is already
        # imported by earlier modules with the suite's store, which is fine: the ledger never goes through ENV_DIR.
        # The suite's CONTEXT_ROOT is restored afterwards, so later modules keep their throw-away store.
        with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root)}):
            spec = importlib.util.spec_from_file_location("session_under_test", BIN / "session.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        (self.root / "sessions").mkdir(exist_ok=True)
        mod._ledger_append({"session": "t3", "heartbeat": "2026-09-26T10:00:00Z"}, {"turns": 3, "tokens": {"cache_read": 10}})
        row = (self.root / "sessions" / "_ledger.md").read_text(encoding="utf-8").splitlines()[-1]
        self.assertTrue(row.startswith("| "), row)
        self.assertIn("| `t3` | - | 3 |", row)
        self.assertEqual(row.count("|"), 15)  # every column present, missing fields as zeros


if __name__ == "__main__":
    unittest.main()
