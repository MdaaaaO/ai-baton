"""ctx_adapter.py — the kit's adapter to ctx-store: one pin, a resolver that says "not installed" cleanly, a pinned
fetch, and the Claude Code hooks (PostToolUse validate/heartbeat, SessionStart briefs) that stay silent no-ops on a
machine without ctx or without a store; plus `session register` stamping the harness session id a hook matches on.

The hooks run against a fake `ctx` (KIT_CTX) that logs its argv, so no test needs the real tool or the network; every
store, cache and scratch path is a temp dir. Session ids and paths are assembled at run time. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
ADAPTER = BIN / "ctx_adapter.py"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402

FAKE_CTX = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["FAKE_CTX_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "store": os.environ.get("CTX_STORE"),
                         "lock": os.environ.get("CTX_LOCK_TIMEOUT")}}) + "\\n")
sys.stdout.write(os.environ.get("FAKE_CTX_OUT", ""))
sys.stderr.write(os.environ.get("FAKE_CTX_ERR", ""))
sys.exit(int(os.environ.get("FAKE_CTX_RC", "0")))
"""

SCRUB = ("KIT_CTX", "CTX_STORE", "CTX_LOCK_TIMEOUT", "CLAUDE_CODE_SESSION_ID", "XDG_CACHE_HOME", "WORKSPACE_TZ")


def load_adapter():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ctx_adapter_under_test", ADAPTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.root = self.t / "ws" / ".context"
        (self.root / "sessions").mkdir(parents=True)
        self.log = self.t / "ctx.log"
        self.fake = self.t / "bin" / "ctx"
        self.fake.parent.mkdir()
        self.fake.write_text(FAKE_CTX, encoding="utf-8")
        self.fake.chmod(0o755)
        self.sid = str(uuid.uuid4())
        self.env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        self.env.update(CONTEXT_ROOT=str(self.root), XDG_CACHE_HOME=str(self.t / "cache"), FAKE_CTX_LOG=str(self.log),
                        KIT_SCRATCH=str(self.t / "scratch"))

    def tearDown(self):
        self.tmp.cleanup()

    def adapter(self, *args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ADAPTER), *args], input=stdin, env={**self.env, **env},
                              capture_output=True, text=True, cwd=self.t, timeout=60)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]

    def payload(self, **kw) -> str:
        return json.dumps({"session_id": self.sid, "cwd": str(self.t), **kw})


class Pin(Base):
    def test_version_prints_the_one_pinned_tag(self):
        mod = load_adapter()
        self.assertRegex(mod.CTX_VERSION, r"^v\d+\.\d+\.\d+$")
        r = self.adapter("version")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, mod.CTX_VERSION))

    def test_the_tag_is_named_in_one_place(self):
        """No other kit file pins a ctx-store version of its own: a second copy would drift from CTX_VERSION."""
        pin = re.compile(r"ctx(?:-store)?\W{0,3}v?\d+\.\d+\.\d+|CTX_VERSION\s*=")
        hits = sorted(str(p.relative_to(KIT)) for p in KIT.rglob("*")
                      if p.is_file() and p.suffix in (".py", ".sh", ".md", ".json", ".mk", ".env", ".yml")
                      and not {".git", ".worktrees", "tests", "__pycache__"} & set(p.relative_to(KIT).parts)
                      and p.name != "CHANGELOG.md" and pin.search(p.read_text(encoding="utf-8", errors="replace")))
        self.assertEqual(hits, ["context-db/bin/ctx_adapter.py"])

    def test_the_pinned_location_carries_the_tag(self):
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.t / "cache")}):
            mod = load_adapter()
            self.assertEqual(mod.pinned_dir(), self.t / "cache" / "ai-baton-kit" / "ctx-store" / mod.CTX_VERSION)


class Resolver(Base):
    def test_not_installed_is_exit_1_with_one_line(self):
        r = self.adapter("where")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("not installed", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_the_override_wins(self):
        r = self.adapter("where", KIT_CTX=str(self.fake))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(self.fake)))

    def test_an_unusable_override_is_not_installed_not_a_fallback(self):
        pinned = self.pin_a_copy()
        r = self.adapter("where", KIT_CTX=str(self.t / "missing" / "ctx"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("KIT_CTX", r.stderr)
        self.assertNotIn(str(pinned), r.stdout)

    def test_the_pinned_install_is_found(self):
        pinned = self.pin_a_copy()
        r = self.adapter("where")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(pinned)))

    def pin_a_copy(self) -> Path:
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.t / "cache")}):
            d = load_adapter().pinned_dir()
        d.mkdir(parents=True)
        (d / "ctx").write_text(FAKE_CTX, encoding="utf-8")
        (d / "ctx").chmod(0o755)
        return d / "ctx"


class Install(Base):
    """The pinned fetch: a shallow clone of exactly the tag, checked against `ctx --version`, renamed into place."""

    def repo(self, tag: str, reports: str) -> str:
        src = self.t / "upstream"
        src.mkdir()
        (src / "ctx").write_text(f"#!{sys.executable}\nprint('ctx {reports} (api 1)')\n", encoding="utf-8")
        (src / "ctx").chmod(0o755)
        env = hermetic_env(self.t)
        for cmd in (["init", "-q"], ["add", "ctx"], ["commit", "-q", "-m", "c"], ["tag", tag]):
            subprocess.run(["git", "-C", str(src), *cmd], env=env, check=True, capture_output=True)
        return str(src)

    def test_install_fetches_the_tag_and_is_idempotent(self):
        mod = load_adapter()
        url = self.repo(mod.CTX_VERSION, mod.CTX_VERSION.lstrip("v"))
        dest = self.t / "cache" / "pinned"
        with mock.patch.dict(os.environ, hermetic_env(self.t)):
            got = mod.install(url=url, dest=dest)
            self.assertEqual(got, dest)
            self.assertTrue(os.access(dest / "ctx", os.X_OK))
            self.assertFalse((dest / ".git").exists())
            self.assertEqual(mod.install(url="/nonexistent/repo", dest=dest), dest)  # present: no second fetch
        self.assertEqual([p.name for p in dest.parent.iterdir()], ["pinned"])  # no temp dir left behind

    def test_a_copy_reporting_another_version_is_refused(self):
        mod = load_adapter()
        url = self.repo(mod.CTX_VERSION, "0.0.1")
        dest = self.t / "cache" / "pinned"
        with mock.patch.dict(os.environ, hermetic_env(self.t)):
            with self.assertRaises(OSError):
                mod.install(url=url, dest=dest)
        self.assertFalse(dest.exists())
        self.assertEqual(list(dest.parent.iterdir()), [])

    def test_a_missing_tag_is_an_error_not_a_half_copy(self):
        mod = load_adapter()
        url = self.repo("v0.0.0-other", "0.0.0")
        dest = self.t / "cache" / "pinned"
        with mock.patch.dict(os.environ, hermetic_env(self.t)):
            with self.assertRaises(OSError):
                mod.install(url=url, dest=dest)
        self.assertFalse(dest.exists())


class HooksAreSilentWithoutCtxOrStore(Base):
    """A machine that has not adopted ctx-store sees nothing: exit 0, no output, no call."""

    def all_hooks(self, **env) -> None:
        under = self.payload(tool_name="Write", tool_input={"file_path": str(self.root / "a.md")})
        for name in ("post-tool-use", "post-tool-use-async", "brief-registry", "brief-session"):
            r = self.adapter("hook", name, stdin=under, **env)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), name)

    def test_not_installed(self):
        self.all_hooks(FAKE_CTX_OUT="should not print")
        self.assertEqual(self.calls(), [])

    def test_an_unusable_override(self):
        self.all_hooks(KIT_CTX=str(self.t / "missing"))
        self.assertEqual(self.calls(), [])

    def test_no_store_named_or_found(self):
        self.all_hooks(KIT_CTX=str(self.fake), CONTEXT_ROOT=str(self.t / "nowhere" / ".context"))
        self.assertEqual(self.calls(), [])

    def test_ctx_says_no_store(self):
        self.all_hooks(KIT_CTX=str(self.fake), FAKE_CTX_RC="2", FAKE_CTX_ERR="NO_STORE x: no store found\n")
        self.assertTrue(self.calls())  # it asked ctx, and ctx's refusal stayed silent

    def test_garbage_on_stdin(self):
        for name in ("post-tool-use", "brief-session"):
            r = self.adapter("hook", name, stdin="{not json", KIT_CTX=str(self.fake))
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), name)

    def test_a_ctx_that_is_not_runnable(self):
        broken = self.t / "bin" / "broken"
        broken.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
        broken.chmod(0o755)
        self.all_hooks(KIT_CTX=str(broken))

    def test_the_wired_commands_are_silent_too(self):
        """The exact hooks.json / settings.json command strings, run by sh, on a machine without ctx."""
        env = {**self.env, "CLAUDE_PLUGIN_ROOT": str(KIT), "CLAUDE_PROJECT_DIR": str(self.t / "proj")}
        (self.t / "proj").mkdir()
        (self.t / "proj" / ".claude").symlink_to(KIT)
        cmds = Wiring.adapter_commands()
        self.assertEqual(len(cmds), 8)  # four hooks on each install path
        for cmd in cmds:
            r = subprocess.run(["sh", "-c", cmd], input=self.payload(), env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), cmd)


class PostToolUse(Base):
    def setUp(self):
        super().setUp()
        self.env["KIT_CTX"] = str(self.fake)

    def write(self, path: Path | str, tool: str = "Write", field: str = "file_path") -> str:
        return self.payload(hook_event_name="PostToolUse", tool_name=tool, tool_input={field: str(path)})

    def test_a_change_outside_the_content_root_calls_nothing(self):
        for name in ("post-tool-use", "post-tool-use-async"):
            r = self.adapter("hook", name, stdin=self.write(self.t / "ws" / "README.md"))
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.calls(), [])

    def test_a_change_under_it_validates_and_adopts_naming_the_store(self):
        r = self.adapter("hook", "post-tool-use", stdin=self.write(self.root / "sessions" / "x.md", tool="Edit"))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        [call] = self.calls()
        self.assertEqual(call["argv"], ["--store", str(self.root), "validate", "--changed", "--adopt"])
        self.assertEqual(call["lock"], "3")  # a held lock cannot stall the tool call for ctx's default 10 s

    def test_a_relative_path_resolves_against_the_payload_cwd(self):
        payload = json.dumps({"session_id": self.sid, "cwd": str(self.root.parent), "tool_name": "Write",
                              "tool_input": {"file_path": ".context/notes.md"}})
        self.adapter("hook", "post-tool-use", stdin=payload)
        self.assertEqual(len(self.calls()), 1)

    def test_a_notebook_edit_counts(self):
        self.adapter("hook", "post-tool-use", stdin=self.write(self.root / "n.ipynb", tool="NotebookEdit", field="notebook_path"))
        self.assertEqual(len(self.calls()), 1)

    def test_findings_come_back_as_a_system_message(self):
        finding = "SCHEMA_VIOLATION sessions/bad frontmatter: schema violation"
        r = self.adapter("hook", "post-tool-use", stdin=self.write(self.root / "sessions" / "bad.md"),
                         FAKE_CTX_RC="3", FAKE_CTX_ERR=finding + "\n")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(set(out), {"systemMessage"})
        self.assertIn(finding, out["systemMessage"])

    def test_any_other_failure_says_validation_did_not_run(self):
        for rc, err in (("4", "LOCK_TIMEOUT x: lock timeout\n"), ("5", "STORE_READONLY x: store is read-only\n"),
                        ("1", "USAGE x: bad command line\n"), ("7", "")):
            r = self.adapter("hook", "post-tool-use", stdin=self.write(self.root / "a.md"), FAKE_CTX_RC=rc, FAKE_CTX_ERR=err)
            self.assertEqual((r.returncode, r.stderr), (0, ""), rc)
            msg = json.loads(r.stdout)["systemMessage"]
            self.assertTrue(msg.startswith("ctx validate did not run: "), msg)
            self.assertIn(err.strip() or "exit 7", msg)

    def test_a_validate_timeout_says_validation_did_not_run(self):
        mod = load_adapter()
        def boom(*a, **k):
            raise mod.subprocess.TimeoutExpired("ctx", mod.HOOK_TIMEOUT)
        mod._ctx = boom
        mod.resolve = lambda: (self.fake, "override")
        mod._context_root = lambda: self.root
        import io
        from unittest import mock
        with mock.patch.dict(os.environ, {"CTX_STORE": ""}), \
             mock.patch.object(sys, "stdin", io.StringIO(self.write(self.root / "a.md"))):
            out = mod.hook("post-tool-use")
        self.assertIn("ctx validate did not run", json.loads(out)["systemMessage"])

    def test_no_store_on_validate_stays_silent(self):
        r = self.adapter("hook", "post-tool-use", stdin=self.write(self.root / "a.md"),
                         FAKE_CTX_RC="2", FAKE_CTX_ERR="NO_STORE x: no store found\n")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_ctx_store_set_is_passed_through_not_overridden(self):
        locator = "memory://" + uuid.uuid4().hex[:8]
        self.adapter("hook", "post-tool-use", stdin=self.write(self.root / "a.md"), CTX_STORE=locator)
        [call] = self.calls()
        self.assertEqual(call["argv"], ["validate", "--changed", "--adopt"])
        self.assertEqual(call["store"], locator)

    def test_the_async_hook_touches_the_session_row_and_needs_its_id(self):
        anonymous = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(self.root / "a.md")}})
        self.assertEqual(self.adapter("hook", "post-tool-use-async", stdin=anonymous).returncode, 0)
        self.assertEqual(self.calls(), [])  # no session id on stdin: nothing to touch
        r = self.adapter("hook", "post-tool-use-async", stdin=self.write(self.root / "a.md"), FAKE_CTX_OUT="touched\n")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        [call] = self.calls()
        self.assertEqual(call["argv"], ["--store", str(self.root), "touch", "--session", self.sid])


class SessionStart(Base):
    def setUp(self):
        super().setUp()
        self.env["KIT_CTX"] = str(self.fake)

    def test_startup_briefs_the_registry_within_a_budget(self):
        brief = "sessions: 1 not ended\n- lane-topic · active\n"
        r = self.adapter("hook", "brief-registry", stdin=self.payload(source="startup"), FAKE_CTX_OUT=brief)
        self.assertEqual((r.returncode, r.stdout), (0, brief))
        [call] = self.calls()
        self.assertEqual(call["argv"][:4], ["--store", str(self.root), "brief", "--registry"])
        self.assertEqual(call["argv"][4], "--budget")
        self.assertLessEqual(int(call["argv"][5]), 4096)

    def test_compact_briefs_this_session(self):
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), FAKE_CTX_OUT="doc\n")
        self.assertEqual((r.returncode, r.stdout), (0, "doc\n"))
        [call] = self.calls()
        self.assertEqual(call["argv"][:5], ["--store", str(self.root), "brief", "--session", self.sid])
        self.assertIn("--budget", call["argv"])

    def test_a_failed_brief_prints_nothing(self):
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), FAKE_CTX_RC="2",
                         FAKE_CTX_OUT="partial", FAKE_CTX_ERR="NO_SUCH_DOC x: no such doc\n")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))


class Wiring(unittest.TestCase):
    """hooks/hooks.json (plugin) and settings.json (clone) run the same adapter hooks on the same events."""

    @staticmethod
    def events(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))["hooks"]

    @classmethod
    def adapter_commands(cls) -> list[str]:
        return [h["command"] for p in (KIT / "hooks" / "hooks.json", KIT / "settings.json")
                for groups in cls.events(p).values() for g in groups for h in g["hooks"] if "ctx_adapter.py" in h["command"]]

    def wired(self, path: Path) -> dict[str, tuple[str, bool]]:
        out = {}
        for event, groups in self.events(path).items():
            for g in groups:
                for h in g["hooks"]:
                    m = re.search(r"ctx_adapter\.py\"? hook ([a-z-]+)", h["command"])
                    if m:
                        out[m.group(1)] = (f"{event}:{g.get('matcher', '')}", bool(h.get("async")))
                        self.assertTrue(h["command"].rstrip().endswith("|| true"), h["command"])
        return out

    def test_both_install_paths_wire_the_same_hooks(self):
        plugin = self.wired(KIT / "hooks" / "hooks.json")
        clone = self.wired(KIT / "settings.json")
        self.assertEqual(plugin, clone)
        edits = plugin["post-tool-use"][0]
        self.assertTrue(edits.startswith("PostToolUse:"))
        for tool in ("Write", "Edit"):
            self.assertRegex(edits.split(":", 1)[1], rf"(^|\|){tool}(\||$)")
        self.assertEqual(plugin["post-tool-use"][1], False)   # validate is synchronous: its finding must surface
        self.assertEqual(plugin["post-tool-use-async"], (edits, True))
        self.assertEqual(plugin["brief-registry"], ("SessionStart:startup|resume|clear", False))
        self.assertEqual(plugin["brief-session"], ("SessionStart:compact", False))
        first = self.events(KIT / "hooks" / "hooks.json")["SessionStart"][0]  # the session-env hook keeps its place
        self.assertNotIn("matcher", first)
        self.assertIn("session-env", first["hooks"][0]["command"])


class SessionIdStamp(Base):
    """`session register` records the harness session id, the key a hook (handed `session_id` on stdin) matches on."""

    def register(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "session.py"), "register", "--name", "lane-topic", "--no-stats",
                               *args], env={**self.env, **env}, capture_output=True, text=True, cwd=BIN, timeout=60)

    def doc(self) -> str:
        return (self.root / "sessions" / "lane-topic.md").read_text(encoding="utf-8")

    def test_register_stamps_the_harness_id_not_the_stats_override(self):
        stats_for = str(uuid.uuid4())  # --session-id / SESSION_ID may name another session's transcript
        r = self.register("--session-id", stats_for, CLAUDE_CODE_SESSION_ID=self.sid)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"\nsession_id: {self.sid}\n", self.doc())
        self.assertNotIn(stats_for, self.doc())

    def test_a_re_register_under_a_new_harness_id_moves_the_stamp(self):
        self.register(CLAUDE_CODE_SESSION_ID=self.sid)
        other = str(uuid.uuid4())
        r = self.register(CLAUDE_CODE_SESSION_ID=other)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = self.doc()
        self.assertIn(f"\nsession_id: {other}\n", doc)
        self.assertNotIn(self.sid, doc)

    def test_without_the_variable_an_earlier_stamp_survives_and_touch_keeps_it(self):
        self.register(CLAUDE_CODE_SESSION_ID=self.sid)
        self.assertEqual(self.register("--working", "x").returncode, 0)
        r = subprocess.run([sys.executable, str(BIN / "session.py"), "touch", "--name", "lane-topic", "--no-stats"],
                           env=self.env, capture_output=True, text=True, cwd=BIN, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"\nsession_id: {self.sid}\n", self.doc())


if __name__ == "__main__":
    unittest.main()
