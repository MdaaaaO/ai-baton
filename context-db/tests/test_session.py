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
                # the exit-code contract: an I/O error is 2 in kb.py / kit_profile.py; session.py still says 1
                self.assertEqual(r.returncode, 1 if script == "session.py" else 2, (script, r.stderr))
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


class RegistrySafety(unittest.TestCase):
    """#132: a name never leaves sessions/, free text reaches the doc verbatim, concurrent writers never tear it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        (self.root / "sessions").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_name_cannot_escape_sessions(self):
        (self.root / "INDEX.md").write_text("store index\n", encoding="utf-8")
        for bad in ("../INDEX", "a/b", "..", ".hidden", "", "x" * 65, "a b"):
            r = run("session.py", "register", "--name", bad, "--no-stats", root=self.root)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual((self.root / "INDEX.md").read_text(encoding="utf-8"), "store index\n")
        self.assertEqual(sorted(p.name for p in (self.root / "sessions").glob("*.md")), [])

    def test_make_passes_free_text_verbatim(self):
        text = """it's "quoted" $(touch PWNED-a) `touch PWNED-b` $$HOME; $(shell touch PWNED-c) \\ end"""
        mk = ["make", "-s", "-C", str(BIN.parent), f"CONTEXT={self.root}", "NOSTATS=1 $(shell touch PWNED-n)", "NAME=t-mk",
              f"WORKING={text}", f"RESP={text}", "SESSION_ID=$(shell touch PWNED-s)"]
        env = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
        r = subprocess.run([*mk[:4], "session-register", *mk[4:]], cwd=self.tmp.name, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = (self.root / "sessions" / "t-mk.md").read_text(encoding="utf-8")
        self.assertIn(f"working_on: {text}\n", doc)
        self.assertIn(f"responsibilities: {text}\n", doc)
        self.assertEqual([p.name for p in Path(self.tmp.name).rglob("PWNED*")] + [p.name for p in BIN.parent.rglob("PWNED*")], [])

    def test_a_newline_cannot_forge_a_field(self):
        r = run("session.py", "register", "--name", "t-nl", "--no-stats", "--working", "a\nstatus: ended", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = (self.root / "sessions" / "t-nl.md").read_text(encoding="utf-8")
        self.assertIn("working_on: a status: ended\n", doc)
        self.assertIn("status: active\n", doc)

    def test_a_body_rule_is_not_front_matter(self):
        r = run("session.py", "register", "--name", "t-rule", "--no-stats", "--epic", "E-9",
                "--note", "above\n\n---\n\nbelow: not a field", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        spec = importlib.util.spec_from_file_location("gen_sessions_rule", BIN / "gen_sessions.py")
        sys.path.insert(0, str(BIN))
        gs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gs)  # type: ignore[union-attr]
        meta = gs.parse(str(self.root / "sessions" / "t-rule.md"))
        self.assertEqual((meta["epic"], meta.get("below")), ("E-9", None))

    def test_concurrent_touches_leave_a_whole_doc(self):
        r = run("session.py", "register", "--name", "t-cc", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        e = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
        e["CONTEXT_ROOT"] = str(self.root)
        procs = [subprocess.Popen([sys.executable, str(BIN / "session.py"), "touch", "--name", "t-cc", "--no-stats",
                                   "--working", f"w{i}"], env=e, cwd=BIN, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for i in range(8)]
        for p in procs:
            _, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err)
        doc = (self.root / "sessions" / "t-cc.md").read_text(encoding="utf-8")
        self.assertEqual(doc.count("\nsession: t-cc\n"), 1)
        self.assertRegex(doc, r"\nworking_on: w\d\n")
        self.assertEqual([p.name for p in (self.root / "sessions").glob(".*.tmp")], [])


class NoSilentLoss(unittest.TestCase):
    """The registry never loses or overwrites state silently."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        (self.root / "sessions").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_note_fills_notes_and_keeps_the_body(self):
        run("session.py", "register", "--name", "t-n", "--no-stats", root=self.root)
        doc = self.root / "sessions" / "t-n.md"
        doc.write_text(doc.read_text(encoding="utf-8") + "\n## Open PRs\n- o/r#1 abc — waits\n", encoding="utf-8")
        r = run("session.py", "register", "--name", "t-n", "--no-stats", "--note", "first note", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        run("session.py", "register", "--name", "t-n", "--no-stats", "--note", "second note", root=self.root)
        text = doc.read_text(encoding="utf-8")
        self.assertIn("## Open PRs\n- o/r#1 abc — waits", text)
        self.assertIn("## Notes\n\nsecond note", text)
        self.assertNotIn("first note", text)

    def test_empty_working_keeps_the_focus(self):
        run("session.py", "register", "--name", "t-w", "--no-stats", "--working", "on #1", root=self.root)
        run("session.py", "touch", "--name", "t-w", "--no-stats", "--working", "", root=self.root)
        self.assertIn("working_on: on #1\n", (self.root / "sessions" / "t-w.md").read_text(encoding="utf-8"))

    def test_a_fenced_heading_is_not_a_section(self):
        spec = importlib.util.spec_from_file_location("session_fence", BIN / "session.py")
        sys.path.insert(0, str(BIN))
        with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root)}):
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        body = "## Next session\n\nold prompt\n```\n## Session stats\nquoted\n```\ntail of prompt\n\n## Open PRs\n- x\n"
        out = mod._replace_section(body, "## Session stats", "NEW")
        self.assertIn("```\n## Session stats\nquoted\n```", out)   # the fenced copy is untouched
        self.assertTrue(out.rstrip().endswith("## Session stats\n\nNEW"), out)
        out = mod._replace_section(body, "## Next session", "new prompt")
        self.assertNotIn("tail of prompt", out)                    # the fence belongs to the section being replaced
        self.assertIn("## Open PRs\n- x", out)

    def test_an_unbalanced_fence_does_not_duplicate_a_section(self):
        spec = importlib.util.spec_from_file_location("session_fence2", BIN / "session.py")
        sys.path.insert(0, str(BIN))
        with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root)}):
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        body = "## Next session\n\nprompt with a stray ```\n\n## Session stats\n\nold\n"
        out = mod._replace_section(body, "## Session stats", "new")
        self.assertEqual(out.count("## Session stats"), 1, out)
        self.assertIn("## Session stats\n\nnew", out)

    def test_a_non_string_tracker_regex_does_not_break_the_registry(self):
        import json as _json
        subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env={**os.environ, "CONTEXT_ROOT": str(self.root)},
                       check=True, capture_output=True)
        cfg = next(self.root.rglob("config.json"))
        data = _json.loads(cfg.read_text(encoding="utf-8"))
        data.setdefault("tracker", {})["key_regex"] = True
        cfg.write_text(_json.dumps(data), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("session_stats_rx", BIN / "session_stats.py")
        with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root)}):
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            with unittest.mock.patch("sys.stderr"):
                rx = mod.ticket_re()
        self.assertIsNone(rx.search("ABC" + "-12 anything"))  # assembled: the leak gate scans added lines

    def test_a_bad_tracker_regex_does_not_break_the_registry(self):
        subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env={**os.environ, "CONTEXT_ROOT": str(self.root)},
                       check=True, capture_output=True)
        subprocess.run([sys.executable, str(BIN / "kb.py"), "config-set", "tracker.key_regex", "([A-Z]+-"],
                       env={**os.environ, "CONTEXT_ROOT": str(self.root)}, capture_output=True)
        r = run("session.py", "register", "--name", "t-rx", root=self.root, env={"CLAUDE_CODE_SESSION_ID": "none"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
