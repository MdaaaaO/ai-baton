"""ctx_sync.py (#515) — the context store synced over git: two clones of one bare remote, every pass driven the way
the hooks and session.py drive it (the script as a subprocess, `CONTEXT_ROOT` naming the clone). The fake ctx of
test_ctx_adapter stands in for ctx-store where a pass validates merged docs; everything else is real git."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import hermetic_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from ctx_sync import ATTR_BEGIN, ATTR_END, ATTR_LINES  # noqa: E402

BIN = Path(__file__).resolve().parents[1] / "bin"
SYNC = BIN / "ctx_sync.py"
ADAPTER = BIN / "ctx_adapter.py"
SESSION = BIN / "session.py"

FAKE_CTX = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["FAKE_CTX_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": sys.argv[1:]}}) + "\\n")
sys.stderr.write(os.environ.get("FAKE_CTX_ERR", ""))
sys.exit(int(os.environ.get("FAKE_CTX_RC", "0")))
"""

DOC = """---
title: Alpha
type: epic
domain: alpha
tags: [alpha]
status: active
updated: 2026-10-01
---

# Alpha

## Remaining work

- first

## Session log

- 2026-10-01 — created
"""


def git(cwd: Path, *args: str, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    """`env` is always a fixture's hermetic_env() dict (Base.setUp builds it; every caller passes it)."""
    r = subprocess.run(["git", "-C", str(cwd), *args], env=env, capture_output=True, text=True,  # hermetic-exempt: env is the fixture's hermetic_env() dict, passed through
                       timeout=60)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr or r.stdout}")
    return r


class Base(unittest.TestCase):
    """A bare remote and two clones, `a` and `b`, each a content root with one doc and the registry dirs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.env = hermetic_env(self.t)
        for k in ("CTX_ACTOR", "KIT_CTX", "CTX_STORE", "CLAUDE_CODE_SESSION_ID", "WORKSPACE_USER", "WORKSPACE_GITHUB_LOGIN"):
            self.env.pop(k, None)
        self.env.update(KIT_SCRATCH=str(self.t / "scratch"), XDG_CACHE_HOME=str(self.t / "cache"), CTX_ACTOR="t")
        self.remote = self.t / "remote.git"
        git(self.t, "init", "-q", "--bare", "-b", "main", str(self.remote), env=self.env)
        self.a = self.t / "a" / ".context"
        self.seed(self.a)
        git(self.a, "init", "-q", "-b", "main", env=self.env)
        git(self.a, "add", "-A", env=self.env)
        git(self.a, "commit", "-q", "-m", "seed", env=self.env)
        git(self.a, "remote", "add", "origin", str(self.remote), env=self.env)
        git(self.a, "push", "-q", "-u", "origin", "main", env=self.env)
        self.b = self.t / "b" / ".context"
        git(self.t, "clone", "-q", str(self.remote), str(self.b), env=self.env)
        self.fake_log = self.t / "ctx.log"

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, root: Path) -> None:
        (root / "alpha").mkdir(parents=True)
        (root / "alpha" / "alpha.md").write_text(DOC, encoding="utf-8")
        (root / "sessions").mkdir()
        (root / "sessions" / "_ledger.md").write_text("# Ledger\n\n| when | who |\n|---|---|\n", encoding="utf-8")
        (root / ".audit").mkdir()
        (root / ".audit" / "seq").write_text("10\n", encoding="utf-8")
        (root / ".audit" / "a.jsonl").write_text('{"seq": 1}\n', encoding="utf-8")
        (root / "reference" / "env").mkdir(parents=True)
        (root / "reference" / "env" / "config.json").write_text(json.dumps({"environment": "t", "context": {"sync": "auto"}}),
                                                                encoding="utf-8")
        (root / ".gitignore").write_text("*.lock\n__pycache__/\n", encoding="utf-8")
        (root / ".gitattributes").write_text("\n".join([ATTR_BEGIN, *ATTR_LINES, ATTR_END]) + "\n", encoding="utf-8")

    def config(self, root: Path, mode: str) -> None:
        p = root / "reference" / "env" / "config.json"
        cfg = json.loads(p.read_text(encoding="utf-8"))
        cfg["context"] = {"sync": mode}
        p.write_text(json.dumps(cfg), encoding="utf-8")

    def run_sync(self, root: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
        e = dict(self.env, CONTEXT_ROOT=str(root), **env)
        return subprocess.run([sys.executable, str(SYNC), *args], env=e, capture_output=True, text=True, timeout=120,
                              cwd=self.t)

    def line(self, root: Path, *args: str, **env: str) -> tuple[int, str]:
        r = self.run_sync(root, "run", *args, **env)
        return r.returncode, r.stdout.strip()

    def remote_log(self) -> list[str]:
        return git(self.t, "--git-dir", str(self.remote), "log", "--format=%s", "main", env=self.env).stdout.splitlines()

    def fake_ctx(self, rc: str = "0", err: str = "") -> dict[str, str]:
        fake = self.t / "fake" / "ctx"
        fake.parent.mkdir(exist_ok=True)
        fake.write_text(FAKE_CTX, encoding="utf-8")
        fake.chmod(0o755)
        return {"KIT_CTX": str(fake), "FAKE_CTX_LOG": str(self.fake_log), "FAKE_CTX_RC": rc, "FAKE_CTX_ERR": err}


class Modes(Base):
    def test_clean_store_is_in_sync(self):
        code, line = self.line(self.a)
        self.assertEqual(code, 0, line)
        self.assertTrue(line.startswith("context sync: clean"), line)

    def test_a_change_is_committed_and_pushed(self):
        (self.a / "alpha" / "alpha.md").write_text(DOC.replace("- first", "- first\n- second"), encoding="utf-8")
        code, line = self.line(self.a)
        self.assertEqual(code, 0, line)
        self.assertIn("pushed", line)
        self.assertEqual(self.remote_log()[0], "context: alpha/alpha.md (t)")
        self.assertEqual(git(self.a, "status", "--porcelain", env=self.env).stdout, "")
        attrs = (self.a / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.md merge=ctx-doc", attrs)
        self.assertIn(".audit/seq merge=ctx-max", attrs)
        self.assertIn(str(SYNC), git(self.a, "config", "--local", "merge.ctx-doc.driver", env=self.env).stdout)

    def test_quiet_hides_a_no_op(self):
        r = self.run_sync(self.a, "run", "--quiet")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_off_wins_over_a_remote(self):
        self.config(self.a, "off")
        (self.a / "alpha" / "note.md").write_text("x\n", encoding="utf-8")
        code, line = self.line(self.a)
        self.assertEqual((code, line), (0, "context sync: off (context.sync: off)"))
        self.assertIn("note.md", git(self.a, "status", "--porcelain", env=self.env).stdout)

    def test_commit_mode_never_pushes(self):
        self.config(self.a, "commit")
        self.line(self.a)  # commits the config change itself
        (self.a / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
        code, line = self.line(self.a)
        self.assertEqual(code, 0, line)
        self.assertIn("committed", line)
        self.assertIn("no push", line)
        self.assertNotIn("alpha/alpha.md", "\n".join(self.remote_log()))

    def test_no_remote_commits_only(self):
        git(self.a, "remote", "remove", "origin", env=self.env)
        (self.a / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
        code, line = self.line(self.a)
        self.assertEqual(code, 0, line)
        self.assertIn("committed", line)
        self.assertIn("no origin remote", line)

    def test_a_store_inside_a_bigger_repo_is_left_alone(self):
        ws = self.t / "ws"
        root = ws / ".context"
        self.seed(root)
        git(ws, "init", "-q", "-b", "main", env=self.env)
        code, line = self.line(root)
        self.assertEqual(code, 0)
        self.assertEqual(line, "context sync: off (the store is not its own git repository)")
        self.assertEqual(git(ws, "log", "--oneline", env=self.env, check=False).stdout, "")
        self.assertEqual(git(ws, "config", "--local", "merge.ctx-doc.driver", env=self.env, check=False).stdout, "")

    def test_push_mode_in_a_plain_directory_is_off(self):
        root = self.t / "plain" / ".context"
        self.seed(root)
        self.config(root, "push")
        code, line = self.line(root)
        self.assertEqual((code, line), (0, "context sync: off (the store is not its own git repository)"))

    def test_status_line(self):
        r = self.run_sync(self.a, "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("push; origin/main"), r.stdout)
        self.assertIn("0 ahead, 0 behind", r.stdout)

    def test_no_network_keeps_the_commit_for_later(self):
        git(self.a, "remote", "set-url", "origin", str(self.t / "gone.git"), env=self.env)
        (self.a / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
        code, line = self.line(self.a)
        self.assertEqual(code, 0, line)
        self.assertIn("push pending", line)
        self.assertEqual(git(self.a, "log", "--format=%s", "-1", env=self.env).stdout.strip(), "context: alpha/alpha.md (t)")
        git(self.a, "remote", "set-url", "origin", str(self.remote), env=self.env)
        code, line = self.line(self.a)
        self.assertIn("pushed", line)

    def test_quiet_pull_is_silent_until_something_comes_in(self):
        r = self.run_sync(self.b, "run", "--no-push", "--quiet")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)  # the session-start hook: nothing to pull, nothing to say
        (self.a / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
        self.line(self.a)
        r = self.run_sync(self.b, "run", "--no-push", "--quiet")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pulled", r.stdout)

    def test_a_gitfile_store_keeps_its_state_in_the_git_dir(self):
        # `.git` a file: a clone with --separate-git-dir (a worktree looks the same to the pass)
        gitdir = self.t / "c.git"
        root = self.t / "c" / ".context"
        git(self.t, "clone", "-q", f"--separate-git-dir={gitdir}", str(self.remote), str(root), env=self.env)
        self.assertTrue((root / ".git").is_file())
        (root / "alpha" / "alpha.md").write_text(DOC + "\n- from c\n", encoding="utf-8")
        code, line = self.line(root)
        self.assertEqual(code, 0, line)
        self.assertIn("pushed", line)
        self.assertEqual(json.loads((gitdir / "ctx-sync" / "status").read_text(encoding="utf-8"))["state"], "ok")
        self.assertTrue((gitdir / "ctx-sync.lock").exists())
        r = self.run_sync(root, "status")
        self.assertTrue(r.stdout.startswith("push; origin/main"), r.stdout)

    def test_commit_identity_falls_back_to_the_actor(self):
        env = {k: v for k, v in self.env.items() if not k.startswith(("GIT_AUTHOR", "GIT_COMMITTER"))}
        env.update(CONTEXT_ROOT=str(self.a), CTX_ACTOR="kit-07", WORKSPACE_GITHUB_LOGIN="someone")  # a session name
        (self.a / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SYNC), "run"], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        who = git(self.a, "log", "-1", "--format=%an <%ae>", env=self.env).stdout.strip()
        self.assertEqual(who, "kit-07 <someone@users.noreply.github.com>")


class TwoClones(Base):
    def test_pull_brings_the_other_clones_change(self):
        (self.a / "alpha" / "alpha.md").write_text(DOC.replace("- first", "- first\n- from a"), encoding="utf-8")
        self.line(self.a)
        code, line = self.line(self.b, "--no-push")
        self.assertEqual(code, 0, line)
        self.assertIn("pulled", line)
        self.assertIn("- from a", (self.b / "alpha" / "alpha.md").read_text(encoding="utf-8"))

    def test_both_sides_append_to_one_doc_and_both_lines_survive(self):
        a_doc = DOC.replace("- 2026-10-01 — created", "- 2026-10-01 — created\n- 2026-10-02 — from a").replace("updated: 2026-10-01", "updated: 2026-10-02")
        b_doc = DOC.replace("- 2026-10-01 — created", "- 2026-10-01 — created\n- 2026-10-03 — from b").replace("updated: 2026-10-01", "updated: 2026-10-03")
        (self.a / "alpha" / "alpha.md").write_text(a_doc, encoding="utf-8")
        (self.b / "alpha" / "alpha.md").write_text(b_doc, encoding="utf-8")
        (self.a / ".audit" / "seq").write_text("11\n", encoding="utf-8")
        (self.b / ".audit" / "seq").write_text("13\n", encoding="utf-8")
        (self.a / ".audit" / "a.jsonl").write_text('{"seq": 1}\n{"seq": 11}\n', encoding="utf-8")
        (self.b / ".audit" / "a.jsonl").write_text('{"seq": 1}\n{"seq": 13}\n', encoding="utf-8")
        with open(self.a / "sessions" / "_ledger.md", "a", encoding="utf-8") as f:
            f.write("| 1 | a |\n")
        with open(self.b / "sessions" / "_ledger.md", "a", encoding="utf-8") as f:
            f.write("| 2 | b |\n")
        code, line = self.line(self.a)
        self.assertEqual(code, 0, line)
        self.assertIn("pushed", line)
        code, line = self.line(self.b)
        self.assertEqual(code, 0, line + git(self.b, "status", env=self.env).stdout)
        self.assertIn("pushed", line)
        merged = (self.b / "alpha" / "alpha.md").read_text(encoding="utf-8")
        self.assertIn("- 2026-10-02 — from a", merged)
        self.assertIn("- 2026-10-03 — from b", merged)
        self.assertEqual(merged.count("---\n"), 2, merged)
        self.assertIn("updated: 2026-10-03", merged)
        self.assertNotIn("<<<<<<<", merged)
        self.assertEqual((self.b / ".audit" / "seq").read_text(encoding="utf-8"), "13\n")
        audit = (self.b / ".audit" / "a.jsonl").read_text(encoding="utf-8")
        self.assertIn('{"seq": 11}', audit)
        self.assertIn('{"seq": 13}', audit)
        ledger = (self.b / "sessions" / "_ledger.md").read_text(encoding="utf-8")
        self.assertIn("| 1 | a |", ledger)
        self.assertIn("| 2 | b |", ledger)
        self.assertNotIn("<<<<<<<", ledger)
        # a's next pass pulls the merged result back, nothing left to push
        code, line = self.line(self.a)
        self.assertEqual(code, 0, line)
        self.assertEqual((self.a / "alpha" / "alpha.md").read_text(encoding="utf-8"), merged)
        subjects = self.remote_log()
        self.assertTrue(any(s.startswith("context: alpha/alpha.md") for s in subjects), subjects)

    def test_merged_docs_go_through_ctx_validate(self):
        a_doc = DOC.replace("- first", "- first\n- from a")
        b_doc = DOC.replace("- 2026-10-01 — created", "- 2026-10-01 — created\n- from b")
        (self.a / "alpha" / "alpha.md").write_text(a_doc, encoding="utf-8")
        (self.b / "alpha" / "alpha.md").write_text(b_doc, encoding="utf-8")
        self.line(self.a)
        code, line = self.line(self.b, **self.fake_ctx("0"))
        self.assertEqual(code, 0, line)
        calls = [json.loads(x)["argv"] for x in self.fake_log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(c[-3:] == ["validate", "--changed", "--adopt"] for c in calls), calls)

    def test_a_validate_finding_undoes_the_rebase_and_keeps_the_commit(self):
        a_doc = DOC.replace("- first", "- first\n- from a")
        b_doc = DOC.replace("- 2026-10-01 — created", "- 2026-10-01 — created\n- from b")
        (self.a / "alpha" / "alpha.md").write_text(a_doc, encoding="utf-8")
        (self.b / "alpha" / "alpha.md").write_text(b_doc, encoding="utf-8")
        self.line(self.a)
        code, line = self.line(self.b, **self.fake_ctx("3", "alpha/alpha: duplicate heading\n"))
        self.assertEqual(code, 3, line)
        self.assertIn("conflict", line)
        self.assertIn("duplicate heading", line)
        # b's own commit is still there, unmerged, and the work tree is b's own doc
        self.assertEqual(git(self.b, "log", "-1", "--format=%s", env=self.env).stdout.strip(), "context: alpha/alpha.md (t)")
        self.assertEqual((self.b / "alpha" / "alpha.md").read_text(encoding="utf-8"), b_doc)
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())
        status = json.loads((self.b / ".git" / "ctx-sync" / "status").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "conflict")
        self.assertEqual(status["paths"], ["alpha/alpha.md"])
        # the remote never saw b's commit
        self.assertNotIn("from b", git(self.t, "--git-dir", str(self.remote), "show", "main:alpha/alpha.md", env=self.env).stdout)
        # the status line and the adapter's hooks surface it until a pass succeeds
        r = self.run_sync(self.b, "status")
        self.assertIn("conflict pending in alpha/alpha.md", r.stdout)
        hook = subprocess.run([sys.executable, str(ADAPTER), "hook", "post-tool-use"], env=dict(self.env, CONTEXT_ROOT=str(self.b)),
                              input=json.dumps({"tool_name": "mcp__ctx__ctx_log", "tool_input": {}}), capture_output=True,
                              text=True, timeout=60)
        self.assertIn("conflict pending in alpha/alpha.md", json.loads(hook.stdout)["systemMessage"])
        # once the merge validates, the pass goes through and the status clears
        code, line = self.line(self.b, **self.fake_ctx("0"))
        self.assertEqual(code, 0, line)
        self.assertIn("pushed", line)
        self.assertEqual(json.loads((self.b / ".git" / "ctx-sync" / "status").read_text(encoding="utf-8"))["state"], "ok")
        self.assertEqual(subprocess.run([sys.executable, str(ADAPTER), "hook", "post-tool-use"], env=dict(self.env, CONTEXT_ROOT=str(self.b)),
                                        input=json.dumps({"tool_name": "mcp__ctx__ctx_log", "tool_input": {}}), capture_output=True,
                                        text=True, timeout=60).stdout, "")

    def test_a_real_git_conflict_is_aborted_and_named(self):
        # a deletes the doc, b edits it: no driver can merge that
        (self.a / "alpha" / "alpha.md").unlink()
        self.line(self.a)
        (self.b / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
        code, line = self.line(self.b)
        self.assertEqual(code, 3, line)
        self.assertIn("conflict", line)
        self.assertIn("alpha/alpha.md", line)
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())
        self.assertEqual(git(self.b, "status", "--porcelain", env=self.env).stdout, "")
        self.assertTrue((self.b / "alpha" / "alpha.md").exists())

    def stopped_rebase_in_b(self) -> None:
        """Both clones append to the end of the doc; b's rebase by hand (no merge drivers configured yet) stops."""
        (self.a / "alpha" / "alpha.md").write_text(DOC + "\n- from a\n", encoding="utf-8")
        self.line(self.a)
        (self.b / "alpha" / "alpha.md").write_text(DOC + "\n- from b\n", encoding="utf-8")
        git(self.b, "add", "-A", env=self.env)
        git(self.b, "commit", "-q", "-m", "by hand", env=self.env)
        git(self.b, "fetch", "-q", "origin", "main", env=self.env)
        r = git(self.b, "rebase", "origin/main", env=self.env, check=False)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.b / ".git" / "rebase-merge").exists())

    def test_a_rebase_a_killed_pass_left_is_undone_by_the_next_pass(self):
        self.stopped_rebase_in_b()
        st = self.b / ".git" / "ctx-sync" / "status"
        st.parent.mkdir(exist_ok=True)
        st.write_text(json.dumps({"state": "running", "detail": "rebase"}), encoding="utf-8")  # what a pass writes before `git rebase`
        r = self.run_sync(self.b, "status")
        self.assertIn("interrupted mid-rebase", r.stdout)
        self.assertNotIn("rebase is in progress", r.stdout)
        code, line = self.line(self.b)
        self.assertEqual(code, 0, line)
        self.assertIn("pushed", line)  # aborted the stale rebase, then merged both lines with the drivers
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())
        merged = git(self.t, "--git-dir", str(self.remote), "show", "main:alpha/alpha.md", env=self.env).stdout
        self.assertIn("- from a", merged)
        self.assertIn("- from b", merged)

    def test_a_rebase_started_by_hand_stops_the_pass_until_it_is_finished(self):
        self.stopped_rebase_in_b()
        code, line = self.line(self.b)
        self.assertEqual(code, 3, line)
        self.assertIn("a rebase is in progress", line)
        self.assertTrue((self.b / ".git" / "rebase-merge").exists())  # untouched
        self.assertIn("a rebase is in progress", self.run_sync(self.b, "status").stdout)
        hook = subprocess.run([sys.executable, str(ADAPTER), "hook", "post-tool-use"], env=dict(self.env, CONTEXT_ROOT=str(self.b)),
                              input=json.dumps({"tool_name": "mcp__ctx__ctx_log", "tool_input": {}}), capture_output=True,
                              text=True, timeout=60)
        self.assertIn("a rebase is in progress", json.loads(hook.stdout)["systemMessage"])
        git(self.b, "rebase", "--abort", env=self.env)
        code, line = self.line(self.b)
        self.assertEqual(code, 0, line)
        self.assertIn("pushed", line)

    def test_brief_registry_shows_the_pending_conflict_without_ctx(self):
        (self.a / "alpha" / "alpha.md").unlink()
        self.line(self.a)
        (self.b / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
        self.assertEqual(self.line(self.b)[0], 3)
        env = dict(self.env, CONTEXT_ROOT=str(self.b))  # no KIT_CTX, no pinned ctx under this cache: ctx is not installed
        hook = subprocess.run([sys.executable, str(ADAPTER), "hook", "brief-registry"], env=env, input="{}",
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertIn("conflict pending in alpha/alpha.md", hook.stdout)

    def test_generated_catalogs_are_regenerated_not_merged(self):
        (self.a / "INDEX.md").write_text("# Index\n\nfrom a\n", encoding="utf-8")
        (self.b / "INDEX.md").write_text("# Index\n\nfrom b\n", encoding="utf-8")
        self.line(self.a)
        code, line = self.line(self.b)
        self.assertEqual(code, 0, line)
        self.assertNotIn("<<<<<<<", (self.b / "INDEX.md").read_text(encoding="utf-8"))
        self.assertIn("alpha", (self.b / "INDEX.md").read_text(encoding="utf-8"))  # gen_index.py wrote it

    def test_a_held_lock_is_reported_busy_not_waited_for(self):
        import fcntl
        lock = self.a / ".git" / "ctx-sync.lock"
        with open(lock, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            (self.a / "alpha" / "alpha.md").write_text(DOC + "\n- more\n", encoding="utf-8")
            code, line = self.line(self.a, "--lock-wait", "0.3")
        self.assertEqual(code, 0, line)
        self.assertIn("busy", line)
        self.assertIn("alpha/alpha.md", git(self.a, "status", "--porcelain", env=self.env).stdout)


class MergeDoc(unittest.TestCase):
    """The frontmatter rules of the `ctx-doc` driver, in process."""

    def setUp(self):
        sys.path.insert(0, str(BIN))
        import importlib
        self.m = importlib.import_module("ctx_sync")

    def test_updated_takes_the_newest_and_a_key_both_changed_follows_it(self):
        base = ["title: A", "status: active", "updated: 2026-10-01", "tags: [a]"]
        ours = ["title: A", "status: closed", "updated: 2026-10-05", "tags: [a]"]
        theirs = ["title: A", "status: paused", "updated: 2026-10-03", "tags: [a, b]"]
        out = self.m.merge_frontmatter(base, ours, theirs)
        self.assertEqual(out, ["title: A", "status: closed", "updated: 2026-10-05", "tags: [a, b]"])

    def test_a_side_that_did_not_change_a_key_yields(self):
        base = ["title: A", "status: active", "updated: 2026-10-01"]
        ours = ["title: A", "status: active", "updated: 2026-10-01"]
        theirs = ["title: B", "status: active", "updated: 2026-10-02", "epic: X-1"]
        self.assertEqual(self.m.merge_frontmatter(base, ours, theirs), ["title: B", "status: active", "updated: 2026-10-02", "epic: X-1"])

    def test_a_file_without_frontmatter_is_a_plain_union(self):
        fm, body = self.m.split_frontmatter("# Ledger\n\n| a |\n")
        self.assertIsNone(fm)
        self.assertEqual(body, "# Ledger\n\n| a |\n")


class SessionRegistry(Base):
    def test_session_register_prints_the_pass(self):
        env = dict(self.env, CONTEXT_ROOT=str(self.a))
        r = subprocess.run([sys.executable, str(SESSION), "register", "--name", "kit-01", "--no-stats", "--working", "x"],
                           env=env, capture_output=True, text=True, timeout=120, cwd=self.t)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("registered sessions/kit-01.md", r.stdout)
        self.assertIn("context sync: pushed", r.stdout)
        self.assertIn("sessions/kit-01.md", git(self.t, "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", "main",
                                               env=self.env).stdout)


if __name__ == "__main__":
    unittest.main()
