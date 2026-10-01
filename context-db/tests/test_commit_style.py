"""commit_style.py — subject/message checks per style, the label mapping, and repo-marker resolution. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import commit_style as cs  # noqa: E402
import kb  # noqa: E402
import kit_profile  # noqa: E402


class BlankStore(unittest.TestCase):
    """A test never reads the machine's store: kit_profile/kb are pointed at a throw-away blank one (and the
    lru caches cleared), and every subprocess gets the same CONTEXT_ROOT."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        env = self.root / "reference" / "env"
        self._saved = (kit_profile.ENV_DIR, kb.ENV)
        kit_profile.ENV_DIR = env
        kb.ENV = env
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        kb.init_blank()
        self.env = {**os.environ, "CONTEXT_ROOT": str(self.root)}

    def tearDown(self):
        kit_profile.ENV_DIR, kb.ENV = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()


class Subjects(BlankStore):
    def test_conventional_accepts_the_kit_shape(self):
        for s in ("feat(dbt): add the fact table (#123)", "fix: release the lock", "refactor(kb)!: drop the profiles layer",
                  "docs(kit): new-environment migration steps", "chore(release): 0.2.0", 'Revert "feat: x"', "Merge branch 'x'",
                  "fixup! feat: x"):
            self.assertEqual(cs.check_subject(s, "conventional"), [], s)

    def test_conventional_rejects_each_rule_separately(self):
        self.assertIn("empty subject", cs.check_subject("   ", "conventional"))
        self.assertTrue(any("not `<type>(<scope>)!: <description>`" in p for p in cs.check_subject("Add the fact table", "conventional")))
        self.assertTrue(any("type `wip`" in p for p in cs.check_subject("wip(kb): x", "conventional")))
        self.assertTrue(any("should start lower-case" in p for p in cs.check_subject("feat(kb): Add x", "conventional")))
        # an acronym leading the description is refused like any other upper-case start, and with no key
        # shape configured (the blank store) so is a key; a digit or a backtick is not an upper-case letter
        for refused in ("API keys", "WHY row in the body", "KEY add x", "KEY-1 add x", "UTF-8 decode the body"):
            self.assertTrue(any("should start lower-case" in p for p in cs.check_subject(f"feat(kb): {refused}", "conventional")), refused)
        self.assertEqual(cs.check_subject("feat(kb): key-1 add x", "conventional"), [])
        self.assertEqual(cs.check_subject("feat(kb): 3 keys rotated", "conventional"), [])
        self.assertEqual(cs.check_subject("feat(kb): `foo` bar baz", "conventional"), [])
        self.assertTrue(any("ends with a period" in p for p in cs.check_subject("feat(kb): add x.", "conventional")))
        self.assertTrue(any("73 chars" in p for p in cs.check_subject("feat(kb): " + "x" * 63, "conventional")))
        self.assertEqual(cs.check_subject("feat(kb): " + "x" * 62, "conventional"), [])

    def test_only_a_configured_tracker_key_may_lead_a_conventional_description(self):
        kb.save_config({**kb.load_config(), "tracker": {"kind": "jira", "key_regex": r"\b(KEY-\d+)\b"}})
        kit_profile.env_config.cache_clear(); kit_profile.load.cache_clear()
        self.assertEqual(cs.check_subject("feat(dbt): KEY-123 add the fact table", "conventional"), [])
        # shaped like a key, but not this environment's key — and a key further in does not excuse the start
        for refused in ("UTF-8 decode the body", "SHA-256 the tokens", "X-1 add x", "KEY add x", "Add x for KEY-1"):
            self.assertTrue(any("should start lower-case" in p for p in cs.check_subject(f"feat(kb): {refused}", "conventional")), refused)
        kb.save_config({**kb.load_config(), "tracker": {"kind": "jira", "key_regex": "(KEY-"}})  # not a regex: no exemption
        kit_profile.env_config.cache_clear(); kit_profile.load.cache_clear()
        self.assertTrue(any("should start lower-case" in p for p in cs.check_subject("feat(kb): KEY-1 add x", "conventional")))

    def test_ticket_key_and_free(self):
        self.assertEqual(cs.check_subject("anything at all.", "free"), [])
        self.assertTrue(any("no tracker.key_regex" in p for p in cs.check_subject("KEY-1: x", "ticket-key")))  # the blank store has none
        kb.save_config({**kb.load_config(), "tracker": {"kind": "jira", "key_regex": r"\b(PROJ-\d+)\b"}})
        kit_profile.env_config.cache_clear(); kit_profile.load.cache_clear()
        self.assertEqual(cs.check_subject("PROJ-1: add x", "ticket-key"), [])
        self.assertTrue(any("not `<KEY>: <description>`" in p for p in cs.check_subject("add x", "ticket-key")))
        # the lower-case-start rule is a conventional-style rule only — ticket-key and free are unchanged
        self.assertEqual(cs.check_subject("PROJ-1: Add x", "ticket-key"), [])
        self.assertEqual(cs.check_subject("Anything at all.", "free"), [])

    def test_message_second_line_must_be_blank_and_comments_are_ignored(self):
        self.assertEqual(cs.check_message("# editor hint\nfeat(kb): add x\n\nbody\n", "conventional"), [])
        self.assertIn("second line must be blank (subject / blank / body)", cs.check_message("feat(kb): add x\nbody\n", "conventional"))
        self.assertEqual(cs.check_message("# only comments\n", "conventional"), ["empty message"])
        self.assertEqual(cs.check_message("; hint\nfeat: x\n", "conventional", comment=";"), [])

    def test_message_rejects_ai_attribution_but_not_a_human_coauthor(self):
        gen = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        self.assertTrue(any("AI attribution" in p for p in
                             cs.check_message(f"feat(kb): add x\n\nbody\n\n{gen}\n", "conventional")))
        self.assertTrue(any("AI attribution" in p for p in
                             cs.check_message("feat(kb): add x\n\nbody\n\nCo-Authored-By: Claude <noreply@example.invalid>\n", "conventional")))
        self.assertEqual(cs.check_message("feat(kb): add x\n\nbody\n\nCo-Authored-By: Alex Doe <alex@example.invalid>\n", "conventional"), [])
        # a human co-author whose first name happens to be Claude — surname distinguishes them from the AI trailer
        self.assertEqual(
            cs.check_message("feat(kb): add x\n\nbody\n\nCo-Authored-By: Claude Martin <claude.martin@example.invalid>\n",
                              "conventional"), [])
        self.assertTrue(any("AI attribution" in p for p in
                             cs.check_message("feat(kb): add x\n\nbody\n\nCo-Authored-By: Claude Sonnet 5 <noreply@example.invalid>\n",
                                               "conventional")))
        # the trailers actually seen carry a dotted version, and a new model family must not slip past the list:
        # an address at the vendor's domain is attribution whatever name precedes it (assembled: no email literal)
        vendor = "noreply@" + "anthropic" + ".com"
        for name in ("Claude Opus 4.5", "Claude Sonnet 4.5", "Claude Haiku 4.5", "Claude Mythos 2"):
            self.assertTrue(any("AI attribution" in p for p in cs.check_message(
                f"feat(kb): add x\n\nbody\n\nCo-Authored-By: {name} <{vendor}>\n", "conventional")), name)
        self.assertTrue(any("AI attribution" in p for p in cs.check_message(
            "feat(kb): add x\n\nbody\n\nCo-Authored-By: Claude Opus 4.5 <noreply@example.invalid>\n", "conventional")))
        human = "jane.doe@" + "anthropic" + ".com"  # a person at the vendor's domain is a real co-author
        self.assertEqual(cs.check_message(f"feat(kb): add x\n\nbody\n\nCo-Authored-By: Jane Doe <{human}>\n",
                                          "conventional"), [])

    def test_label_for(self):
        self.assertEqual(cs.label_for("feat(kb): add x"), "enhancement")
        self.assertEqual(cs.label_for("fix: y"), "bug")
        self.assertEqual(cs.label_for("docs(kit): z"), "documentation")
        self.assertEqual(cs.label_for("chore: z"), "")
        self.assertEqual(cs.label_for("KEY-1: z"), "")


class ConfigLoad(unittest.TestCase):
    def test_no_store_is_the_default_a_broken_one_is_an_error(self):
        saved = (cs.kit_profile.env_config, cs.kit_profile.load)
        try:
            cs.kit_profile.env_config = lambda: {}          # outside a workspace: the kit default, silently
            self.assertEqual(cs._config(), {})

            def broken(*a, **k):
                raise SystemExit("config.json: invalid JSON")
            cs.kit_profile.env_config = broken              # a store that exists but cannot be read: reported
            with self.assertRaises(SystemExit):
                cs._config()

            cs.kit_profile.env_config = lambda: {"commits": {}}

            def interrupted(*a, **k):
                raise KeyboardInterrupt
            cs.kit_profile.load = interrupted
            with self.assertRaises(KeyboardInterrupt):
                cs._config()
        finally:
            cs.kit_profile.env_config, cs.kit_profile.load = saved


class ExitCodes(BlankStore):
    """0 ok · 1 style broken · 2 config or I/O error; `#123 …` is a subject, `# …` a comment."""

    def cs(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "commit_style.py"), *args], input=stdin, capture_output=True,
                              text=True, env=self.env)

    def test_comment_lines_follow_git_and_core_commentchar(self):
        # git's cleanup=strip drops EVERY line starting with the comment char, so the hook must too (review of #224):
        # a `#123 …` subject is gone after git's cleanup; with core.commentChar=';' it is a subject
        self.assertEqual(cs.check_message("#123 fix crash\n", "free"), ["empty message"])
        self.assertEqual(cs.check_message("#123 fix crash\n; a comment\n", "free", comment=";"), [])
        self.assertEqual(cs.check_message("fix: x\n\n# Please enter the message\n#\n", "conventional"), [])

    def test_a_broken_commits_override_is_exit_2(self):
        kb.save_config({**kb.load_config(), "commits": {"default": "shouty"}})
        p = self.cs("check", "-", stdin="fix: x\n")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("is not one of", p.stderr)
        self.assertNotIn("does not follow", p.stderr)

    def test_an_unreadable_message_file_is_exit_2(self):
        p = self.cs("check", "/nonexistent/msg")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_style_violation_is_exit_1(self):
        p = self.cs("check", "--style", "conventional", "-", stdin="Fixed things\n")
        self.assertEqual(p.returncode, 1, p.stderr)

    def test_a_crash_is_exit_2_never_the_style_code(self):
        with mock.patch.object(cs, "_main", side_effect=KeyError("boom")), mock.patch("sys.stderr"):
            self.assertEqual(cs.main(["check", "-"]), 2)

    def test_ticket_key_style_without_a_usable_key_shape_is_exit_2(self):
        # no message can satisfy a key shape that is missing or does not compile: a config error, not a style one
        p = self.cs("check", "--style", "ticket-key", "-", stdin="KEY-123: x\n")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("no tracker.key_regex", p.stderr)
        self.assertNotIn("does not follow", p.stderr)
        kb.save_config({**kb.load_config(), "tracker": {"kind": "jira", "key_regex": "(KEY-"}})
        p = self.cs("title", "--style", "ticket-key", "KEY-123: x")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("not a valid regex", p.stderr)
        self.assertNotIn("does not follow", p.stderr)


class Resolve(BlankStore):
    def setUp(self):
        super().setUp()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()

    def test_marker_wins_then_commitlint_then_conventional_release_then_default(self):
        self.assertEqual(cs.resolve(self.repo), ("conventional", "env config commits.default"))  # the blank store's default
        (self.repo / ".conventional-release.toml").write_text("[x]\n", encoding="utf-8")
        self.assertEqual(cs.resolve(self.repo)[1], "conventional-release config in the repo root")
        (self.repo / "commitlint.config.js").write_text("module.exports = {}\n", encoding="utf-8")
        self.assertEqual(cs.resolve(self.repo)[1], "commitlint config in the repo root")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "commit-style").write_text("ticket-key\n", encoding="utf-8")
        self.assertEqual(cs.resolve(self.repo)[0], "ticket-key")
        (self.repo / ".claude" / "commit-style").write_text("haiku\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            cs.resolve(self.repo)

    def test_pyproject_table_and_package_json_key(self):
        (self.repo / "pyproject.toml").write_text("[tool.conventional-release]\nx = 1\n", encoding="utf-8")
        self.assertIn("pyproject.toml", cs.resolve(self.repo)[1])
        (self.repo / "pyproject.toml").unlink()
        (self.repo / "package.json").write_text('{"commitlint": {}}', encoding="utf-8")
        self.assertIn("package.json", cs.resolve(self.repo)[1])
        (self.repo / "package.json").write_text("not json", encoding="utf-8")
        self.assertEqual(cs.resolve(self.repo), ("conventional", "env config commits.default"))

    def test_store_overrides_are_honoured_and_the_kit_default_stands_without_a_store(self):
        kb.save_config({**kb.load_config(), "commits": {"default": "free", "repos": {"acme/widgets": "ticket-key"}}})
        kit_profile.env_config.cache_clear(); kit_profile.load.cache_clear()
        self.assertEqual(cs.resolve(self.repo), ("free", "env config commits.default"))
        self.assertEqual(cs.resolve(self.repo, repo="acme/widgets"), ("ticket-key", "env config commits.repos[acme/widgets]"))
        (self.env_dir() / "config.json").unlink()
        kit_profile.env_config.cache_clear(); kit_profile.load.cache_clear()
        self.assertEqual(cs.resolve(self.repo), ("conventional", "kit default"))

    def env_dir(self) -> Path:
        return self.root / "reference" / "env"

    def test_cli_title_check_and_exit_codes(self):
        py = [sys.executable, str(BIN / "commit_style.py")]
        ok = subprocess.run([*py, "title", "--style", "conventional", "feat(kb): add x"], capture_output=True, text=True, env=self.env)
        self.assertEqual((ok.returncode, ok.stdout.strip()), (0, "ok · conventional (--style)"))
        bad = subprocess.run([*py, "title", "--style", "conventional", "Add x"], capture_output=True, text=True, env=self.env)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("PR title does not follow the conventional style", bad.stderr)
        # the pr-title workflow's pinned release tool rejects this title with "the subject should start
        # lower-case" — the local gate must refuse it too, with matching wording, not just pass it through
        why = subprocess.run([*py, "title", "--style", "conventional", "feat(pr-open): WHY row in the PR body (#1)"],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(why.returncode, 1)
        self.assertIn("should start lower-case", why.stderr)
        why_ok = subprocess.run([*py, "title", "--style", "conventional", "feat(pr-open): why row in the PR body (#1)"],
                                 capture_output=True, text=True, env=self.env)
        self.assertEqual((why_ok.returncode, why_ok.stdout.strip()), (0, "ok · conventional (--style)"))
        msg = self.repo / "msg.txt"
        msg.write_text("fix(sync): keep the lock\n\nbody\n", encoding="utf-8")
        chk = subprocess.run([*py, "check", "--dir", str(self.repo), str(msg)], capture_output=True, text=True, env=self.env)
        self.assertEqual(chk.returncode, 0, chk.stderr)
        stdin = subprocess.run([*py, "check", "--style", "free", "-"], input="whatever\n", capture_output=True, text=True, env=self.env)
        self.assertEqual(stdin.returncode, 0)
        usage = subprocess.run([*py, "check"], capture_output=True, text=True, env=self.env)
        self.assertEqual(usage.returncode, 2)
        types = subprocess.run([*py, "types"], capture_output=True, text=True, env=self.env)
        self.assertEqual(types.stdout.split(), list(cs.TYPES))


if __name__ == "__main__":
    unittest.main()
