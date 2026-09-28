"""Every rewrite of a tracked or store file goes through fsutil.atomic_write (#135): a temp file in the
same directory, fsync'd, then os.replace — so a crash between truncate and write can never leave a
half-written config.json, fact table, INDEX.md or migrated SKILL.md. Each test here monkeypatches
fsutil.os.replace to raise partway through a write and asserts the ORIGINAL file is untouched: on
origin/main (plain `Path.write_text()`/`open(...).write()`) the patch has no effect at all — the write
completes and overwrites the file with the new content, so these tests fail there and pass only once the
write goes through atomic_write."""
from __future__ import annotations
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))

import fsutil  # noqa: E402
import kb  # noqa: E402
import kit_profile  # noqa: E402
import migrate_frontmatter  # noqa: E402


class BreakReplace:
    """A context manager that makes fsutil's os.replace raise once, so a caller's atomic_write() call fails
    partway through — after the temp file is fully written, before it replaces the target."""

    def __enter__(self):
        self._patch = mock.patch.object(fsutil.os, "replace", side_effect=OSError("disk full (simulated)"))
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def no_tmp_siblings(d: Path, name: str) -> bool:
    """True when no `.{name}.*.tmp` litter is left in `d` — atomic_write() unlinks its temp file on failure."""
    return not any(p.name.startswith(f".{name}.") and p.name.endswith(".tmp") for p in d.iterdir())


class KbStoreWrites(unittest.TestCase):
    """kb.py: write_doc (set/rm/migrate), set_fact's new-doc path, drop_empty_section, save_config, init_blank."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR)
        kb.ENV = self.env
        kb.CTX = Path(self.tmp.name)
        kb.ROOT = Path(self.tmp.name).parent
        kit_profile.ENV_DIR = self.env
        kb.init_blank()

    def tearDown(self):
        kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR = self._saved
        self.tmp.cleanup()

    def test_set_fact_leaves_doc_untouched_on_a_failed_replace(self):
        kb.set_fact("slack", "channel", "eng", "C1", "eng chat", "user")
        before = kb.doc_path("slack").read_text(encoding="utf-8")
        with BreakReplace(), self.assertRaises(OSError):
            kb.set_fact("slack", "channel", "eng", "C2", "eng chat", "user")
        self.assertEqual(kb.doc_path("slack").read_text(encoding="utf-8"), before)
        self.assertTrue(no_tmp_siblings(self.env, "slack.md"))

    def test_save_config_leaves_config_untouched_on_a_failed_replace(self):
        cfg = kb.load_config()
        before = kb.config_path().read_text(encoding="utf-8")
        cfg["environment"] = "changed"
        with BreakReplace(), self.assertRaises(OSError):
            kb.save_config(cfg)
        self.assertEqual(kb.config_path().read_text(encoding="utf-8"), before)
        self.assertTrue(no_tmp_siblings(self.env, "config.json"))

    def test_drop_empty_section_leaves_doc_untouched_on_a_failed_replace(self):
        # a blank store's notion.md already carries an empty "## database" section (DEFAULT_KINDS) —
        # exactly what drop_empty_section() removes, no setup needed beyond init_blank() in setUp()
        before = kb.doc_path("notion").read_text(encoding="utf-8")
        with BreakReplace(), self.assertRaises(OSError):
            kb.drop_empty_section("notion", "database")
        self.assertEqual(kb.doc_path("notion").read_text(encoding="utf-8"), before)


class GenIndexWrite(unittest.TestCase):
    """gen_index.py writes INDEX.md through fsutil.atomic_write."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "reference" / "env").mkdir(parents=True)
        (self.root / "reference" / "env" / "config.json").write_text('{"environment":"local","systems":{}}\n', encoding="utf-8")
        (self.root / "INDEX.md").write_text("stale placeholder\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_untouched_on_a_failed_replace(self):
        import importlib
        import os as _os
        env = {**_os.environ, "CONTEXT_ROOT": str(self.root)}
        with mock.patch.dict(_os.environ, env, clear=True):
            sys.path.insert(0, str(BIN))
            try:
                gi = importlib.reload(importlib.import_module("gen_index"))
            finally:
                sys.path.pop(0)
            before = (self.root / "INDEX.md").read_text(encoding="utf-8")
            with BreakReplace(), self.assertRaises(OSError):
                gi.main()
            self.assertEqual((self.root / "INDEX.md").read_text(encoding="utf-8"), before)


class MigrateFrontmatterWrite(unittest.TestCase):
    """migrate_frontmatter.py rewrites a unit's frontmatter through fsutil.atomic_write."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "SKILL.md"
        self.p.write_text(
            "---\nname: demo\ndescription: a demo unit\nversion: 6\nupdated: 2026-09-01\n---\n\nbody\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_file_leaves_file_untouched_on_a_failed_replace(self):
        before = self.p.read_text(encoding="utf-8")
        changes = migrate_frontmatter.migrate_file(self.p, write=False)
        self.assertTrue(changes)  # sanity: this file really is pending a migration
        with BreakReplace(), self.assertRaises(OSError):
            migrate_frontmatter.migrate_file(self.p, write=True)
        self.assertEqual(self.p.read_text(encoding="utf-8"), before)


class ModePreservation(unittest.TestCase):
    """atomic_write() must not settle an existing file at mkstemp's 0600 — a doc rewritten over and over
    (set_fact, save_config, migrate_file, …) keeps whatever mode it already had."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "doc.md"
        self.p.write_text("before\n", encoding="utf-8")
        self.p.chmod(0o644)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_existing_0644_file_stays_0644_after_a_rewrite(self):
        fsutil.atomic_write(str(self.p), "after\n")
        self.assertEqual(self.p.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.p.read_text(encoding="utf-8"), "after\n")


if __name__ == "__main__":
    unittest.main()
