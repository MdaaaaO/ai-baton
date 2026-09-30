"""session.py / kit_profile without an env store, with a broken one, and the registry doc round trip. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import argparse
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
        r = run("session.py", "register", "--name", "t-one", "--no-stats", "--epic", "E-1", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.count("no env store"), 1)
        self.assertTrue((self.root / "sessions" / "t-one.md").is_file())
        self.assertTrue((self.root / "SESSION_INDEX.md").is_file())
        r = run("session.py", "touch", "--name", "t-one", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("session.py", "end", "--name", "t-one", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("status: ended", (self.root / "sessions" / "t-one.md").read_text(encoding="utf-8"))
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
        r = run("session.py", "register", "--name", "t-two", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.root / "sessions" / "t-two.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("updated:", "owner_note: keep me\nupdated:", 1), encoding="utf-8")
        r = run("session.py", "touch", "--name", "t-two", "--no-stats", "--working", "x", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text(encoding="utf-8")
        self.assertIn("owner_note: keep me", text)
        self.assertIn("working_on: x", text)
        self.assertEqual(text.count("session: t-two"), 1)

    def _load_session_mod(self):
        # session.py computes CTX (and so the ledger path) from CONTEXT_ROOT at exec time — kit_profile is already
        # imported by earlier modules with the suite's store, which is fine: the ledger never goes through ENV_DIR.
        # The suite's CONTEXT_ROOT is restored afterwards, so later modules keep their throw-away store.
        with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root)}):
            spec = importlib.util.spec_from_file_location("session_under_test", BIN / "session.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    def test_ledger_row_tolerates_a_partial_stats_dict(self):
        mod = self._load_session_mod()
        (self.root / "sessions").mkdir(exist_ok=True)
        mod._ledger_append({"session": "t3", "heartbeat": "2026-09-26T10:00:00Z"}, {"turns": 3, "tokens": {"cache_read": 10}})
        row = (self.root / "sessions" / "_ledger.md").read_text(encoding="utf-8").splitlines()[-1]
        self.assertTrue(row.startswith("| "), row)
        self.assertIn("| `t3` | - | 3 |", row)
        self.assertEqual(row.count("|"), 15)  # every column present, missing fields as zeros

    def test_a_new_ledger_starts_with_the_type_frontmatter(self):
        # types/ledger.json's doc needs the frontmatter every ctx-store doc needs; a brand new ledger gets it up front.
        mod = self._load_session_mod()
        (self.root / "sessions").mkdir(exist_ok=True)
        mod._ledger_append({"session": "new1", "heartbeat": "2026-09-26T10:00:00Z"}, {"turns": 1})
        text = (self.root / "sessions" / "_ledger.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\ntitle: Session ledger\ntype: ledger\ndomain: sessions\n---\n\n"), text[:200])
        self.assertIn("# Session ledger", text)
        self.assertIn("| `new1` |", text)

    def test_an_existing_ledger_without_frontmatter_gets_it_prepended_once(self):
        # A ledger written before this fix has no frontmatter; the next append gives it one, in place, once — not
        # on every subsequent append.
        mod = self._load_session_mod()
        (self.root / "sessions").mkdir(exist_ok=True)
        ledger = self.root / "sessions" / "_ledger.md"
        old_row = "| 2026-09-25 09:00 | `s0` | - | 1 | 0.0 | 0 | 0 | 0 | 0 (0+0) | 0 | 0 | 0 | 0 | 0 |\n"
        ledger.write_text(mod.LEDGER_HEADER + old_row, encoding="utf-8")
        mod._ledger_append({"session": "s1", "heartbeat": "2026-09-26T11:00:00Z"}, {"turns": 2})
        text = ledger.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(mod.LEDGER_FRONTMATTER), text[:200])
        self.assertIn("| `s0` |", text)
        self.assertIn("| `s1` |", text)
        self.assertEqual(text.count("title: Session ledger"), 1)  # prepended once
        mod._ledger_append({"session": "s2", "heartbeat": "2026-09-26T12:00:00Z"}, {"turns": 3})  # a second append: idempotent
        text2 = ledger.read_text(encoding="utf-8")
        self.assertEqual(text2.count("title: Session ledger"), 1)
        self.assertIn("| `s2` |", text2)

    def test_an_existing_ledger_with_frontmatter_is_left_alone(self):
        # The owner's live ledger already carries this frontmatter (added by hand): a session must not touch it,
        # only append its row.
        mod = self._load_session_mod()
        (self.root / "sessions").mkdir(exist_ok=True)
        ledger = self.root / "sessions" / "_ledger.md"
        old_row = "| 2026-09-25 09:00 | `s0` | - | 1 | 0.0 | 0 | 0 | 0 | 0 (0+0) | 0 | 0 | 0 | 0 | 0 |\n"
        original = mod.LEDGER_FRONTMATTER + mod.LEDGER_HEADER + old_row
        ledger.write_text(original, encoding="utf-8")
        mod._ledger_append({"session": "s1", "heartbeat": "2026-09-26T11:00:00Z"}, {"turns": 2})
        text = ledger.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original), text[:200])  # the existing frontmatter+header+row untouched
        self.assertEqual(text.count("title: Session ledger"), 1)
        self.assertIn("| `s1` |", text)


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

    def test_a_hash_reference_is_quoted_not_truncated_by_a_real_yaml_reader(self):
        """`--working "fix PR #261 review"` contains ' #' — read bare, a real YAML parser treats everything
        from the '#' on as a comment and silently drops it. quoted_value (session.py) must quote it."""
        r = run("session.py", "register", "--name", "t-hash", "--no-stats", "--working", "fix PR #261 review", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = (self.root / "sessions" / "t-hash.md").read_text(encoding="utf-8")
        self.assertIn('working_on: "fix PR #261 review"\n', doc)
        # and a value that IS just a leading '#' comment marker
        r = run("session.py", "touch", "--name", "t-hash", "--no-stats", "--working", "#261 only", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = (self.root / "sessions" / "t-hash.md").read_text(encoding="utf-8")
        self.assertIn('working_on: "#261 only"\n', doc)

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
        # the newline collapses to a space (one_line), and the result contains ': ' — quoted on write so the
        # block stays valid YAML, and so `status:` here reads as text inside the quotes, never a real field
        self.assertIn('working_on: "a status: ended"\n', doc)
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
        # "on #1" contains ' #' — YAML reads that as a comment unquoted, so it is written quoted; the point of
        # this test (an empty --working leaves the stored focus alone) is unaffected
        self.assertIn('working_on: "on #1"\n', (self.root / "sessions" / "t-w.md").read_text(encoding="utf-8"))

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

    def test_empty_content_withdraws_a_stored_section(self):
        # A closing session with nothing to hand over must be able to withdraw a stale next-session
        # prompt, not just leave it un-updated — `_replace_section(..., content="")` drops the section
        # entirely rather than writing an empty one a reader would still trip over.
        spec = importlib.util.spec_from_file_location("session_withdraw", BIN / "session.py")
        sys.path.insert(0, str(BIN))
        with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root)}):
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        body = "## Next session\n\nold prompt, now stale\n\n## Open PRs\n- o/r#1 abc — waits\n"
        out = mod._replace_section(body, "## Next session", "")
        self.assertNotIn("## Next session", out)
        self.assertNotIn("old prompt, now stale", out)
        self.assertIn("## Open PRs\n- o/r#1 abc — waits", out)
        # withdrawing a section that was never there is a no-op, not an error
        untouched = "## Open PRs\n- o/r#1 abc — waits\n"
        self.assertEqual(mod._replace_section(untouched, "## Next session", ""), untouched)

    def test_next_none_withdraws_a_stored_prompt_end_to_end(self):
        # `--next none` (via `make session-end NEXT=none`) withdraws a stored next-session prompt
        # instead of being read as a literal filename `none`.
        run("session.py", "register", "--name", "t-next-none", "--no-stats", root=self.root)
        next_file = self.root / "next.txt"
        next_file.write_text("Register as t-next-none-2. Read the context doc first.\n", encoding="utf-8")
        r = run("session.py", "touch", "--name", "t-next-none", "--no-stats", "--next", str(next_file), root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc_path = self.root / "sessions" / "t-next-none.md"
        self.assertIn("## Next session\n\nRegister as t-next-none-2.", doc_path.read_text(encoding="utf-8"))
        r = run("session.py", "end", "--name", "t-next-none", "--no-stats", "--next", "none", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        doc = doc_path.read_text(encoding="utf-8")
        self.assertNotIn("## Next session", doc)
        self.assertNotIn("Register as t-next-none-2.", doc)
        self.assertIn("status: ended", doc)

    def test_end_adds_a_missing_open_prs_heading(self):
        # session-register step 4 (a successor's re-arm step) reads `## Open PRs` to know which watches
        # to re-arm; a session that ends without ever writing the section must still leave it behind, so
        # a missing heading is never mistaken for "nothing to re-arm".
        run("session.py", "register", "--name", "t-end-noprs", "--no-stats", root=self.root)
        doc_path = self.root / "sessions" / "t-end-noprs.md"
        self.assertNotIn("## Open PRs", doc_path.read_text(encoding="utf-8"))
        r = run("session.py", "end", "--name", "t-end-noprs", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## Open PRs\n\nnone", doc_path.read_text(encoding="utf-8"))

    def test_end_keeps_an_existing_open_prs_list(self):
        run("session.py", "register", "--name", "t-end-hasprs", "--no-stats", root=self.root)
        doc_path = self.root / "sessions" / "t-end-hasprs.md"
        doc_path.write_text(doc_path.read_text(encoding="utf-8") + "\n## Open PRs\n- o/r#1 abc — waits\n", encoding="utf-8")
        r = run("session.py", "end", "--name", "t-end-hasprs", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = doc_path.read_text(encoding="utf-8")
        self.assertIn("## Open PRs\n- o/r#1 abc — waits", doc)
        self.assertEqual(doc.count("## Open PRs"), 1)

    def test_touch_adds_a_missing_open_prs_heading(self):
        run("session.py", "register", "--name", "t-touch-noprs", "--no-stats", root=self.root)
        doc_path = self.root / "sessions" / "t-touch-noprs.md"
        self.assertNotIn("## Open PRs", doc_path.read_text(encoding="utf-8"))
        r = run("session.py", "touch", "--name", "t-touch-noprs", "--no-stats", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## Open PRs\n\nnone", doc_path.read_text(encoding="utf-8"))

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


class NameConventionAndFooter(unittest.TestCase):
    """New registrations follow `<lane>-<topic>[-n]`; the session's name reaches the PR footer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        (self.root / "sessions").mkdir(parents=True)
        self.scratch = Path(self.tmp.name) / "scratch"
        self.env = {"KIT_SCRATCH": str(self.scratch)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_names_must_follow_the_convention(self):
        for bad in ("t1", "Kit-Hardening", "kit_hardening", "kit--x", "kit-" + "x" * 30):
            r = run("session.py", "register", "--name", bad, "--no-stats", root=self.root, env=self.env)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("naming convention", r.stderr)
        for good in ("kit-hardening", "kit-216-changelog", "kit-weekly-2"):
            r = run("session.py", "register", "--name", good, "--no-stats", root=self.root, env=self.env)
            self.assertEqual(r.returncode, 0, (good, r.stderr))

    def test_an_existing_name_outside_the_convention_keeps_working(self):
        (self.root / "sessions" / "legacy1.md").write_text("---\nsession: legacy1\nstatus: active\n---\n\nbody\n",
                                                              encoding="utf-8")
        r = run("session.py", "register", "--name", "legacy1", "--no-stats", root=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # a grandfathered name keeps working but never reaches the public footer
        self.assertEqual(run("kit_profile.py", "session-name", root=self.root, env=self.env).returncode, 1)
        self.assertNotIn("legacy1", run("kit_profile.py", "footer", root=self.root, env=self.env).stdout)

    def test_the_footer_names_the_registered_session(self):
        r = run("kit_profile.py", "footer", root=self.root, env=self.env)
        self.assertEqual(r.stdout.strip(), "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
        self.assertEqual(run("kit_profile.py", "session-name", root=self.root, env=self.env).returncode, 1)
        run("session.py", "register", "--name", "kit-footer-test", "--no-stats", root=self.root, env=self.env)
        self.assertEqual(run("kit_profile.py", "session-name", root=self.root, env=self.env).stdout.strip(), "kit-footer-test")
        self.assertTrue(run("kit_profile.py", "footer", root=self.root, env=self.env).stdout.strip()
                        .endswith("· session `kit-footer-test`"))


def _load_session_module(root: Path):
    """A fresh `session` module bound to `root` as CONTEXT_ROOT — same pattern the fence tests above use.
    Rename tests import the module directly (never `run()`/subprocess) so `_restart_heartbeat` can be
    monkeypatched: a real `session.py rename` subprocess would invoke the real heartbeat.sh, which — run
    from inside an actual Claude session, as these tests are — finds the real owning `claude` process and
    spawns a genuine background heartbeat that outlives the test. Never do that from a test."""
    spec = importlib.util.spec_from_file_location(f"session_mod_{id(root)}", BIN / "session.py")
    with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(root)}):
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class RenameSubcommand(unittest.TestCase):
    """session.py rename --from <old> --to <new>: moves sessions/<old>.md, rewrites the `session:`
    frontmatter and the `# Session: <name>` title, and (best-effort) restarts the heartbeat under the
    new name. `_restart_heartbeat` is always monkeypatched here — see `_load_session_module`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        (self.root / "sessions").mkdir(parents=True)
        self.mod = _load_session_module(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _register(self, name: str, working: str = "") -> None:
        a = argparse.Namespace(name=name, session_id="", no_stats=True, ref="", status="", epic="",
                                repos="", working=working, resp="", note="", next="")
        self.mod.cmd_register(a)

    def test_rename_moves_the_file_and_rewrites_frontmatter_and_title(self):
        self._register("t-old-lane", working="on something")
        with unittest.mock.patch.object(self.mod, "_restart_heartbeat", return_value=True) as rh:
            self.mod.cmd_rename(argparse.Namespace(old="t-old-lane", new="kit-real-topic"))
        rh.assert_called_once_with("t-old-lane", "kit-real-topic", "on something")
        self.assertFalse((self.root / "sessions" / "t-old-lane.md").exists())
        doc = (self.root / "sessions" / "kit-real-topic.md").read_text(encoding="utf-8")
        self.assertIn("session: kit-real-topic", doc)
        self.assertIn("# Session: kit-real-topic", doc)
        self.assertNotIn("t-old-lane", doc)

    def test_rename_refuses_when_the_target_already_has_an_entry(self):
        self._register("t-old-lane")
        self._register("kit-taken-name")
        with unittest.mock.patch.object(self.mod, "_restart_heartbeat", return_value=True):
            with self.assertRaises(SystemExit):
                self.mod.cmd_rename(argparse.Namespace(old="t-old-lane", new="kit-taken-name"))
        self.assertTrue((self.root / "sessions" / "t-old-lane.md").exists())  # untouched
        self.assertIn("session: kit-taken-name", (self.root / "sessions" / "kit-taken-name.md").read_text())

    def test_rename_rejects_a_new_name_outside_the_convention(self):
        self._register("t-old-lane")
        with self.assertRaises(SystemExit):
            self.mod.cmd_rename(argparse.Namespace(old="t-old-lane", new="NotKebabCase"))
        self.assertTrue((self.root / "sessions" / "t-old-lane.md").exists())  # untouched, nothing renamed

    def test_rename_reports_but_does_not_fail_when_the_heartbeat_restart_fails(self):
        self._register("t-old-lane")
        with unittest.mock.patch.object(self.mod, "_restart_heartbeat", return_value=False):
            self.mod.cmd_rename(argparse.Namespace(old="t-old-lane", new="kit-real-topic"))  # no raise
        self.assertTrue((self.root / "sessions" / "kit-real-topic.md").exists())  # the move still happened


class HeartbeatRestartHelpers(unittest.TestCase):
    """`_kill_heartbeat` / `_restart_heartbeat` in isolation. `_restart_heartbeat`'s subprocess call is
    always mocked — see `_load_session_module` above for why a real invocation must never happen in a test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = _load_session_module(Path(self.tmp.name) / ".context")  # content root unused by these helpers

    def tearDown(self):
        self.tmp.cleanup()

    def test_kill_heartbeat_stops_a_live_process_and_removes_the_pidfile(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            pidfile = Path(self.tmp.name) / f"heartbeat-t-live.pid"
            pidfile.write_text(str(proc.pid), encoding="utf-8")
            with unittest.mock.patch.object(self.mod, "_baton_tmp", return_value=self.tmp.name):
                self.assertTrue(self.mod._kill_heartbeat("t-live"))
            proc.wait(timeout=5)
            self.assertFalse(pidfile.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_kill_heartbeat_cleans_up_a_stale_pidfile(self):
        pidfile = Path(self.tmp.name) / "heartbeat-t-stale.pid"
        pidfile.write_text("999999999", encoding="utf-8")  # practically guaranteed not to exist
        with unittest.mock.patch.object(self.mod, "_baton_tmp", return_value=self.tmp.name):
            self.assertFalse(self.mod._kill_heartbeat("t-stale"))
        self.assertFalse(pidfile.exists())

    def test_kill_heartbeat_with_no_pidfile_is_a_noop(self):
        with unittest.mock.patch.object(self.mod, "_baton_tmp", return_value=self.tmp.name):
            self.assertFalse(self.mod._kill_heartbeat("t-never-started"))

    def test_restart_heartbeat_reports_success(self):
        with unittest.mock.patch.object(self.mod, "_kill_heartbeat", return_value=True), \
             unittest.mock.patch.object(self.mod.subprocess, "run",
                                         return_value=subprocess.CompletedProcess([], 0, "heartbeat for kit-x started", "")):
            self.assertTrue(self.mod._restart_heartbeat("old", "kit-x", "focus"))

    def test_restart_heartbeat_reports_failure_without_raising(self):
        with unittest.mock.patch.object(self.mod, "_kill_heartbeat", return_value=True), \
             unittest.mock.patch.object(self.mod.subprocess, "run",
                                         return_value=subprocess.CompletedProcess([], 1, "", "could not find the owning claude process")):
            self.assertFalse(self.mod._restart_heartbeat("old", "kit-x", "focus"))

    def test_restart_heartbeat_starts_nothing_when_none_was_running(self):
        """No heartbeat for the OLD name means the rename isn't of a live session of the caller's own — for
        an ended session, or someone else's, starting one anyway would attach heartbeat.sh to the caller's
        own `claude` process and stats (REVIEW.md § 2.3): it must not even try."""
        with unittest.mock.patch.object(self.mod, "_kill_heartbeat", return_value=False), \
             unittest.mock.patch.object(self.mod.subprocess, "run") as run:
            self.assertFalse(self.mod._restart_heartbeat("old", "kit-x", "focus"))
        run.assert_not_called()
