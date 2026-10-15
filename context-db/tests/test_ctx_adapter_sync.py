"""#550 — the Bash route `ctx_adapter.py ctx <verb>` runs the context-sync pass after a successful write, as the
ctx-store PostToolUse hook does for an MCP write. A store that is its own git repository with a bare remote, a fake
ctx standing in for ctx-store (it appends a line to a doc on a write verb); everything else is real git. tmp dirs
only, no network, never the real `.context/`."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import hermetic_env

BIN = Path(__file__).resolve().parents[1] / "bin"
ADAPTER = BIN / "ctx_adapter.py"

FAKE_CTX = f"""#!{sys.executable}
import sys
args = sys.argv[1:]
store = args[1]  # --store <root>
rest, i = [], 0
while i < len(args):  # ctx's globals: --store/--now take a value (the last --store wins), the verb comes next
    if args[i] in ("--store", "--now"):
        if args[i] == "--store":
            store = args[i + 1]
        i += 2
    else:
        rest.append(args[i])
        i += 1
verb = rest[0]
if "signal" in rest:
    import os, signal
    os.kill(os.getpid(), signal.SIGTERM)
if verb in ("log", "fail"):
    with open(store + "/alpha/alpha.md", "a", encoding="utf-8") as f:
        f.write("- " + " ".join(rest[1:]) + "\\n")
sys.exit(1 if verb == "fail" else 0)
"""


def git(cwd: Path, *args: str, env: dict) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], env=env, capture_output=True, text=True, timeout=60)  # hermetic-exempt: env is the fixture's hermetic_env() dict
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr or r.stdout}")
    return r.stdout


class AdapterSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name).resolve()
        self.env = hermetic_env(self.t)
        for k in ("CTX_ACTOR", "KIT_CTX", "CTX_STORE", "CLAUDE_CODE_SESSION_ID", "CTX_ADAPTER_NO_SYNC"):
            self.env.pop(k, None)
        self.remote = self.t / "remote.git"
        git(self.t, "init", "-q", "--bare", "-b", "main", str(self.remote), env=self.env)
        self.root = self.t / "ws" / ".context"
        (self.root / "alpha").mkdir(parents=True)
        (self.root / "alpha" / "alpha.md").write_text(
            "---\ntitle: Alpha\ntype: project\ndomain: alpha\nstatus: active\nupdated: 2026-01-01\n---\n"
            "# Alpha\n\n## Session log\n\n", encoding="utf-8")
        (self.root / "reference" / "env").mkdir(parents=True)
        (self.root / "reference" / "env" / "config.json").write_text(
            json.dumps({"environment": "t", "context": {"sync": "auto"}}), encoding="utf-8")
        git(self.root, "init", "-q", "-b", "main", env=self.env)
        git(self.root, "add", "-A", env=self.env)
        git(self.root, "commit", "-q", "-m", "seed", env=self.env)
        git(self.root, "remote", "add", "origin", str(self.remote), env=self.env)
        git(self.root, "push", "-q", "-u", "origin", "main", env=self.env)
        self.fake = self.t / "bin" / "ctx"
        self.fake.parent.mkdir()
        self.fake.write_text(FAKE_CTX, encoding="utf-8")
        self.fake.chmod(0o755)
        self.env.update(CONTEXT_ROOT=str(self.root), KIT_CTX=str(self.fake), KIT_SCRATCH=str(self.t / "scratch"),
                        XDG_CACHE_HOME=str(self.t / "cache"), CTX_ACTOR="t")

    def tearDown(self):
        self.tmp.cleanup()

    def adapter(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ADAPTER), "ctx", *args], env={**self.env, **env}, cwd=self.t,
                              capture_output=True, text=True, timeout=120)

    def status(self) -> str:
        return git(self.root, "status", "--porcelain", env=self.env)

    def heads(self) -> tuple:
        return (git(self.root, "rev-parse", "HEAD", env=self.env).strip(),
                git(self.t, "--git-dir", str(self.remote), "rev-parse", "main", env=self.env).strip())

    def test_a_write_verb_commits_and_pushes(self):
        before = self.heads()[1]
        r = self.adapter("log", "alpha/alpha", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("context sync: pushed", r.stderr)
        self.assertEqual(r.stdout, "")  # ctx's own stdout is untouched by the sync line
        self.assertEqual(self.status(), "")
        local, remote = self.heads()
        self.assertEqual(local, remote)
        self.assertNotEqual(remote, before)

    def test_no_sync_env_leaves_the_store_dirty(self):
        before = self.heads()
        r = self.adapter("log", "alpha/alpha", "hello", CTX_ADAPTER_NO_SYNC="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("context sync", r.stderr)
        self.assertIn("alpha/alpha.md", self.status())
        self.assertEqual(self.heads(), before)

    def test_a_read_verb_is_not_synced(self):
        (self.root / "alpha" / "other.md").write_text("x\n", encoding="utf-8")
        before = self.heads()
        r = self.adapter("get", "alpha/alpha")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("other.md", self.status())
        self.assertEqual(self.heads(), before)

    def test_sync_off_is_silent_and_leaves_the_store_dirty(self):
        cfg = self.root / "reference" / "env" / "config.json"
        cfg.write_text(json.dumps({"environment": "t", "context": {"sync": "off"}}), encoding="utf-8")
        r = self.adapter("log", "alpha/alpha", "hello")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertIn("alpha/alpha.md", self.status())

    def test_a_failed_write_is_not_synced_and_keeps_ctxs_exit_code(self):
        before = self.heads()
        r = self.adapter("fail", "alpha/alpha")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("context sync", r.stderr)
        self.assertIn("alpha/alpha.md", self.status())
        self.assertEqual(self.heads(), before)

    def test_an_unreachable_remote_still_exits_zero(self):
        git(self.root, "remote", "set-url", "origin", str(self.t / "gone.git"), env=self.env)
        r = self.adapter("log", "alpha/alpha", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("context sync:", r.stderr)
        self.assertEqual(self.status(), "")  # committed locally, push pending

    def test_the_catalogs_are_fresh_in_the_committed_tree(self):
        self.assertNotIn("INDEX.md", git(self.root, "ls-files", env=self.env))
        r = self.adapter("log", "alpha/alpha", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status(), "")
        committed = git(self.root, "show", "HEAD:INDEX.md", env=self.env)  # regenerated before the commit
        self.assertIn("alpha/alpha.md", committed)

    def test_a_value_taking_option_does_not_hide_the_verb(self):
        before = self.heads()[1]
        r = self.adapter("--store", str(self.root), "--now", "2026-01-01T00:00:00Z", "log", "alpha/alpha", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("context sync: pushed", r.stderr)
        self.assertEqual(self.status(), "")
        self.assertNotEqual(self.heads()[1], before)

    def test_a_signal_death_exits_128_plus_n(self):
        r = self.adapter("log", "alpha/alpha", "signal")  # the fake ctx kills itself with SIGTERM
        self.assertEqual(r.returncode, 128 + 15)


class AdapterSyncUnits(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(BIN))
        self.addCleanup(sys.path.remove, str(BIN))
        import ctx_adapter
        self.adapter = ctx_adapter

    def test_verb_skips_option_values(self):
        v = self.adapter._verb
        self.assertEqual(v(["log", "d", "x"]), "log")
        self.assertEqual(v(["--store", "/p", "log"]), "log")
        self.assertEqual(v(["--now", "t", "--store", "/p", "--json", "fm"]), "fm")
        self.assertEqual(v(["--store=/p", "log"]), "log")
        self.assertEqual(v(["--json", "memory"]), "memory")
        self.assertEqual(v(["--store"]), "")
        self.assertEqual(v(["--", "log"]), "")
        self.assertIn("memory", self.adapter.WRITE_VERBS)

    def test_the_pass_refreshes_catalogs_first_and_carries_the_hook_timeouts(self):
        import ctx_sync
        calls = []
        with mock.patch.object(self.adapter, "refresh_catalogs", lambda root: calls.append(("refresh", root))), \
                mock.patch.object(ctx_sync, "run", lambda root, **kw: calls.append(("run", root, kw)) or (0, "clean")):
            self.adapter._sync_after_write(Path("/x"))
        self.assertEqual(calls, [("refresh", Path("/x")),
                                 ("run", Path("/x"), {"timeout": self.adapter.SYNC_NET_TIMEOUT,
                                                      "lock_wait": self.adapter.SYNC_LOCK_WAIT})])


if __name__ == "__main__":
    unittest.main()
