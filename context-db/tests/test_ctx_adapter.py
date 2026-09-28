"""ctx_adapter.py — the kit's adapter to ctx-store: one pin, a resolver that says "not installed" cleanly, a pinned
fetch, the Claude Code hooks (PreToolUse deny, PostToolUse validate/heartbeat/catalog refresh, SessionStart briefs)
that stay silent no-ops on a machine without ctx or without a store, `adopt` (the one-time store bootstrap), the MCP
server and the Bash route; plus `session register` stamping the harness session id a hook matches on.

Most tests run against a fake `ctx` (KIT_CTX) that logs its argv, so they need neither the real tool nor the network;
the end-to-end ones use the pinned install when this machine has it and skip cleanly otherwise. Every store, cache
and scratch path is a temp dir. Session ids and paths are assembled at run time. Stdlib unittest.
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
                         "lock": os.environ.get("CTX_LOCK_TIMEOUT"), "actor": os.environ.get("CTX_ACTOR")}}) + "\\n")
sys.stdout.write(os.environ.get("FAKE_CTX_OUT", ""))
sys.stderr.write(os.environ.get("FAKE_CTX_ERR", ""))
sys.exit(int(os.environ.get("FAKE_CTX_RC", "0")))
"""

SCRUB = ("KIT_CTX", "CTX_STORE", "CTX_LOCK_TIMEOUT", "CTX_ACTOR", "CLAUDE_CODE_SESSION_ID", "XDG_CACHE_HOME",
         "WORKSPACE_TZ")


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
        lines = r.stdout.splitlines()
        self.assertEqual((r.returncode, lines[0]), (0, mod.CTX_VERSION))
        self.assertEqual(lines[1], f"api {mod.CTX_API}")

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
        for name in ("pre-tool-use", "post-tool-use", "post-tool-use-async", "brief-registry", "brief-session"):
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
        self.assertEqual(len(cmds), 12)  # six hook entries on each install path
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

    def wired(self, path: Path) -> dict[str, list[tuple[str, bool]]]:
        out: dict[str, list[tuple[str, bool]]] = {}
        for event, groups in self.events(path).items():
            for g in groups:
                for h in g["hooks"]:
                    m = re.search(r"ctx_adapter\.py\"? hook ([a-z-]+)", h["command"])
                    if m:
                        out.setdefault(m.group(1), []).append((f"{event}:{g.get('matcher', '')}", bool(h.get("async"))))
                        self.assertTrue(h["command"].rstrip().endswith("|| true"), h["command"])
        return out

    def test_both_install_paths_wire_the_same_hooks(self):
        plugin = self.wired(KIT / "hooks" / "hooks.json")
        clone = self.wired(KIT / "settings.json")
        self.assertEqual(plugin, clone)
        [(edits, sync)] = plugin["post-tool-use"]
        self.assertTrue(edits.startswith("PostToolUse:"))
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            self.assertRegex(edits.split(":", 1)[1], rf"(^|\|){tool}(\||$)")
        self.assertEqual(sync, False)   # validate is synchronous: its finding must surface
        self.assertEqual(plugin["pre-tool-use"], [(edits.replace("PostToolUse", "PreToolUse"), False)])  # a deny must block
        [(on_edit, a1), (on_ctx, a2)] = plugin["post-tool-use-async"]
        self.assertEqual((on_edit, a1, a2), (edits, True, True))
        mcp_matcher = on_ctx.split(":", 1)[1]
        mod = load_adapter()
        for tool in ("mcp__plugin_ai-baton_ctx__ctx_log", "mcp__ctx__ctx_str_replace", "mcp__ctx__ctx_create"):
            self.assertRegex(tool, rf"^(?:{mcp_matcher})$")
            self.assertTrue(mod.CTX_WRITE_TOOL.match(tool), tool)
        for tool in ("mcp__ctx__ctx_get", "mcp__ctx__ctx_find", "mcp__ctx__ctx_touch", "mcp__other__ctx_log"):
            self.assertFalse(mod.CTX_WRITE_TOOL.match(tool), tool)  # a read (or a touch) refreshes nothing
        self.assertEqual(plugin["brief-registry"], [("SessionStart:startup|resume|clear", False)])
        self.assertEqual(plugin["brief-session"], [("SessionStart:compact", False)])
        first = self.events(KIT / "hooks" / "hooks.json")["SessionStart"][0]  # the session-env hook keeps its place
        self.assertNotIn("matcher", first)
        self.assertIn("session-env", first["hooks"][0]["command"])


def real_ctx() -> Path | None:
    """The pinned ctx this machine has installed (its real cache, not a test's), else None: the end-to-end tests skip."""
    mod = load_adapter()
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(cache) / "ai-baton-kit" / "ctx-store" / mod.CTX_VERSION / "ctx"
    return p if p.is_file() and os.access(p, os.X_OK) else None


REAL_CTX = real_ctx()
EPIC = ("---\ntitle: A\ntype: epic\ndomain: d\nstatus: active\nupdated: 2026-01-05\n---\n# A\n\n## Goal\n\ng\n\n"
        "## Key decisions & gotchas\n\n## Remaining work\n\n## Session log\n\n- 2026-01-05 — first\n")


class StoreData(unittest.TestCase):
    """The kit ships the store settings and type schemas `adopt` writes: data the deny and the catalogs agree with."""

    def test_settings_and_schemas_parse(self):
        data = KIT / "context-db" / "ctx-store"
        settings = json.loads((data / "ctx-store.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["schema_version"], 1)
        self.assertTrue({"INDEX.md", "SESSION_INDEX.md"} <= set(settings["generated"]))  # the kit's generators write them
        self.assertIn("memory/**", settings["ignore"])  # the harness auto-memory is never a store doc
        types = sorted(p.stem for p in (data / "types").glob("*.json"))
        self.assertTrue({"epic", "session", "ledger", "log", "self-assessment"} <= set(types), types)
        for t in types:
            self.assertIsInstance(json.loads((data / "types" / f"{t}.json").read_text(encoding="utf-8")), dict, t)
        epic = json.loads((data / "types" / "epic.json").read_text(encoding="utf-8"))
        self.assertEqual(epic["log"], {"section": "Session log", "order": "newest-first"})  # ctx_log keeps the rule

    def test_the_plugin_ships_the_mcp_server(self):
        servers = json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(servers["ctx"], {"command": "python3",
                                          "args": ["${CLAUDE_PLUGIN_ROOT}/context-db/bin/ctx_adapter.py", "mcp"]})
        self.assertEqual(json.loads((KIT / "settings.json").read_text(encoding="utf-8"))["enabledMcpjsonServers"], ["ctx"])


class Adopt(Base):
    """`adopt`: `ctx init --upgrade` with the kit's settings and types (never a store file written by the kit itself),
    then validate and adopt; a store file edited here is kept (exit 5) until `--replace`."""

    def data(self) -> dict[str, str]:
        d = KIT / "context-db" / "ctx-store"
        out = {"ctx-store.json": (d / "ctx-store.json").read_text(encoding="utf-8")}
        out.update({f".ctx/types/{p.name}": p.read_text(encoding="utf-8") for p in (d / "types").glob("*.json")})
        return out

    def test_not_installed_is_exit_1_and_writes_nothing(self):
        for args in (("adopt",), ("adopt", "--check")):
            r = self.adapter(*args)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("not installed", r.stderr)
        self.assertFalse((self.root / "ctx-store.json").exists())

    def test_no_content_root_is_exit_2(self):
        r = self.adapter("adopt", KIT_CTX=str(self.fake), CONTEXT_ROOT=str(self.t / "nowhere"))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_adopt_hands_the_kit_data_to_ctx_init_and_writes_no_store_file(self):
        r = self.adapter("adopt", KIT_CTX=str(self.fake), FAKE_CTX_OUT="ok: 0 docs checked\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = KIT / "context-db" / "ctx-store"
        self.assertEqual([c["argv"][2:] for c in self.calls()],
                         [["init", "--settings", str(data / "ctx-store.json"), "--types", str(data / "types"), "--upgrade"],
                          ["validate"], ["validate", "--changed", "--adopt"]])
        self.assertEqual({c["argv"][1] for c in self.calls()}, {str(self.root)})
        self.assertFalse((self.root / "ctx-store.json").exists())
        self.assertFalse((self.root / ".ctx").exists())

    def test_check_never_adopts(self):
        r = self.adapter("adopt", "--check", KIT_CTX=str(self.fake), FAKE_CTX_RC="2",
                         FAKE_CTX_ERR=f"NO_STORE {self.root}: no store found\n")
        self.assertEqual(r.returncode, 4)
        self.assertIn("ctx_adapter.py adopt", r.stdout)
        self.assertEqual([c["argv"][2:] for c in self.calls()], [["validate"]])
        self.assertFalse((self.root / "ctx-store.json").exists())

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_adopt_inits_the_kit_settings_and_keeps_a_changed_file_until_replace(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        (self.root / "d").mkdir()
        (self.root / "d" / "a.md").write_text(EPIC, encoding="utf-8")
        self.assertEqual(self.adapter("adopt", "--check", **env).returncode, 4)
        self.assertFalse((self.root / "ctx-store.json").exists())
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 adopted", r.stdout)
        for rel, text in self.data().items():
            if rel.startswith(".ctx/"):  # the marker is ctx's own layout of the same settings, not a byte copy
                self.assertEqual((self.root / rel).read_text(encoding="utf-8"), text, rel)
        self.assertEqual(self.adapter("adopt", "--check", **env).returncode, 0)
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)  # a second run changes nothing
        mine = '{"frontmatter": {"title": {"required": true}}}\n'  # the user changed a schema: a finding, kept
        (self.root / ".ctx" / "types" / "log.json").write_text(mine, encoding="utf-8")
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)  # not 3: that code is validation findings
        self.assertIn("differs: .ctx/types/log.json", r.stdout)
        self.assertIn("adopt --replace", r.stdout)
        self.assertIn("adopt: ok", r.stdout)  # validate and adopt still ran
        self.assertEqual((self.root / ".ctx" / "types" / "log.json").read_text(encoding="utf-8"), mine)
        self.assertEqual(self.adapter("adopt", "--replace", **env).returncode, 0)
        self.assertEqual((self.root / ".ctx" / "types" / "log.json").read_text(encoding="utf-8"),
                         self.data()[".ctx/types/log.json"])

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_a_schema_an_earlier_kit_wrote_takes_the_new_kits_copy(self):
        """A kit update: the store holds what the previous kit's `init` wrote, so `adopt` replaces it, no --replace."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        old = self.t / "old-kit-types"
        old.mkdir()
        (old / "log.json").write_text('{"frontmatter": {"title": {"required": true}}}\n', encoding="utf-8")
        subprocess.run([str(REAL_CTX), "--store", str(self.root), "init", "--types", str(old)], check=True,
                       capture_output=True, env=dict(os.environ, CTX_NO_WALK="1"))
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("differs:", r.stdout)
        self.assertEqual((self.root / ".ctx" / "types" / "log.json").read_text(encoding="utf-8"),
                         self.data()[".ctx/types/log.json"])

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_findings_are_printed_and_exit_3(self):
        (self.root / "d").mkdir()
        (self.root / "d" / "bad.md").write_text(EPIC.replace("status: active", "status: someday"), encoding="utf-8")
        r = self.adapter("adopt", KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"(?m)^finding: SCHEMA_VIOLATION d/bad")
        self.assertTrue((self.root / "ctx-store.json").exists())

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_nested_templates_and_a_session_ledger_validate_clean(self):
        """A nested `_templates/` scaffold (placeholder frontmatter) and a ledger `session.py end` just wrote are
        both store-settings exemptions, not findings, on a freshly adopted store."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        tdir = self.root / "x" / "_templates"
        tdir.mkdir(parents=True)
        (tdir / "y.md").write_text(
            "---\ntitle: {{TITLE}}\ntype: repo\ndomain: {{DOMAIN}}\ntags: []\nstatus: reference\nupdated: {{DATE}}\n"
            "---\n\n# {{TITLE}}\n", encoding="utf-8")
        import importlib.util
        with mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root)}):
            spec = importlib.util.spec_from_file_location("session_under_test_e2e", BIN / "session.py")
            session_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(session_mod)  # type: ignore[union-attr]
        session_mod._ledger_append({"session": "e2e", "heartbeat": "2026-09-28T10:00:00Z"}, {"turns": 1})
        self.assertTrue((self.root / "sessions" / "_ledger.md").read_text(encoding="utf-8").startswith("---\n"))
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("finding:", r.stdout)


class PreToolUseDeny(Base):
    """A direct Write/Edit of a store doc is denied on an adopted store; everything else gets no decision."""

    def setUp(self):
        super().setUp()
        self.env["KIT_CTX"] = str(self.fake)
        self.env.update(FAKE_CTX_RC="2", FAKE_CTX_ERR="NO_SUCH_DOC x: no such doc\n")  # ctx: a store, a new doc

    def decide(self, path: Path | str, tool: str = "Edit", field: str = "file_path", **env) -> dict | None:
        payload = self.payload(hook_event_name="PreToolUse", tool_name=tool, tool_input={field: str(path)})
        r = self.adapter("hook", "pre-tool-use", stdin=payload, **env)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def test_a_store_doc_is_denied_with_the_ctx_tool_to_use(self):
        for tool in ("Write", "Edit", "MultiEdit"):
            out = self.decide(self.root / "d" / "a.md", tool=tool)
            self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"), tool)
            for word in ("key `d/a`", "ctx_str_replace", "ctx_create", "ctx_log", "ctx_adapter.py ctx"):
                self.assertIn(word, out["permissionDecisionReason"])
        self.assertEqual(self.calls()[-1]["argv"], ["--store", str(self.root), "get", "d/a"])

    def test_an_existing_doc_is_a_store_doc_too(self):
        self.assertIsNotNone(self.decide(self.root / "d" / "a.md", FAKE_CTX_RC="0", FAKE_CTX_ERR=""))

    def test_a_readme_at_any_level_stays_writable(self):
        # gen_index skips README.md at every level and the shipped settings ignore `**/README.md`, so no ctx verb
        # writes one: a deny would leave a folder's local standards file with no way to change it
        self.assertIsNone(self.decide(self.root / "pr-reviews" / "README.md"))
        self.assertIn("**/README.md", json.loads((KIT / "context-db" / "ctx-store" / "ctx-store.json").read_text())["ignore"])

    def test_a_relative_path_resolves_against_the_payload_cwd(self):
        payload = json.dumps({"cwd": str(self.root.parent), "tool_name": "Write",
                              "tool_input": {"file_path": ".context/d/x.md"}})
        r = self.adapter("hook", "pre-tool-use", stdin=payload)
        self.assertIn('"deny"', r.stdout)

    def test_exempt_paths_get_no_decision_and_no_ctx_call(self):
        memory = self.t / "harness" / "memory"
        memory.mkdir(parents=True)
        (self.root / "memory").symlink_to(memory)
        allowed = ["sessions/lane-topic.md", "sessions/archive/old.md", "state/pr-review/notes.md", "bin/x.md",
                   "reference/env/_templates/t.md", "on-call/handoff/page.md", "handoff/x.md", "memory/MEMORY.md",
                   "INDEX.md", "SESSION_INDEX.md", "README.md", "self-assessment/README.md", "d/notes.txt", "d/n.ipynb", ".ctx/types/epic.json",
                   ".audit/seq"]
        for rel in allowed:
            self.assertIsNone(self.decide(self.root / rel), rel)
        self.assertIsNone(self.decide(memory / "note.md"))  # the symlink's target, named directly
        self.assertIsNone(self.decide(self.root / "d" / "n.ipynb", tool="NotebookEdit", field="notebook_path"))
        self.assertIsNone(self.decide(self.t / "ws" / "repo" / "README.md"))  # outside the content root
        self.assertEqual(self.calls(), [])

    def test_a_real_memory_dir_is_exempt_too(self):
        (self.root / "memory").mkdir()
        self.assertIsNone(self.decide(self.root / "memory" / "note.md"))

    def test_no_decision_unless_ctx_says_it_is_a_store(self):
        doc = self.root / "d" / "a.md"
        self.assertIsNone(self.decide(doc, FAKE_CTX_ERR=f"NO_STORE {self.root}: no store found\n"))  # not adopted
        self.assertIsNone(self.decide(doc, FAKE_CTX_RC="4", FAKE_CTX_ERR="LOCK_TIMEOUT x: lock timeout\n"))
        self.assertIsNone(self.decide(doc, FAKE_CTX_RC="3", FAKE_CTX_ERR="SCHEMA_VIOLATION .ctx/types/x.json: bad\n"))
        self.assertIsNone(self.decide(doc, KIT_CTX=str(self.t / "missing")))  # not installed
        self.assertIsNone(self.decide(self.t / "none" / ".context" / "a.md",
                                      CONTEXT_ROOT=str(self.t / "none" / ".context")))  # no content root on disk

    def test_ctx_store_does_not_redirect_the_check(self):
        self.decide(self.root / "d" / "a.md", CTX_STORE="memory://elsewhere")
        self.assertEqual(self.calls()[-1]["argv"][:2], ["--store", str(self.root)])

    def test_fails_open_on_any_adapter_error(self):
        broken = self.t / "bin" / "broken"
        broken.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
        broken.chmod(0o755)
        self.assertIsNone(self.decide(self.root / "d" / "a.md", KIT_CTX=str(broken)))  # ctx cannot even start
        for bad in ("{not json", json.dumps({"tool_name": "Edit", "tool_input": ["x"]}), json.dumps([1])):
            r = self.adapter("hook", "pre-tool-use", stdin=bad)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), bad)
        import contextlib
        import io
        mod = load_adapter()
        mod.STORE_DATA = self.t / "no-such-data"  # the kit's own settings unreadable: an exception inside the hook
        out = io.StringIO()
        payload = self.payload(tool_name="Edit", tool_input={"file_path": str(self.root / "d" / "a.md")})
        with mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(self.root), "KIT_CTX": str(self.fake),
                                          "FAKE_CTX_LOG": str(self.log)}), \
             mock.patch.object(sys, "stdin", io.StringIO(payload)), contextlib.redirect_stdout(out):
            rc = mod.main(["hook", "pre-tool-use"])
        self.assertEqual((rc, out.getvalue()), (0, ""))

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_end_to_end_with_the_pinned_ctx(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1", FAKE_CTX_RC="0", FAKE_CTX_ERR="")
        (self.root / "d").mkdir()
        (self.root / "d" / "a.md").write_text(EPIC, encoding="utf-8")
        self.assertIsNone(self.decide(self.root / "d" / "a.md", **env))  # not adopted yet: nothing changes
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        self.assertEqual(self.decide(self.root / "d" / "a.md", **env)["permissionDecision"], "deny")
        self.assertEqual(self.decide(self.root / "d" / "new.md", **env)["permissionDecision"], "deny")
        self.assertIsNone(self.decide(self.root / "sessions" / "x.md", **env))


class McpServer(Base):
    def mcp(self, *msgs: dict, **env) -> list[dict]:
        r = self.adapter("mcp", stdin="".join(json.dumps(m) + "\n" for m in msgs), **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]

    def test_execs_the_pinned_ctx_on_the_content_root_as_a_stable_actor(self):
        self.adapter("mcp", KIT_CTX=str(self.fake))
        [call] = self.calls()
        self.assertEqual((call["argv"], call["actor"]), (["--store", str(self.root), "mcp"], "claude"))

    def test_a_set_actor_and_store_pass_through(self):
        self.adapter("mcp", KIT_CTX=str(self.fake), CTX_ACTOR="someone", CTX_STORE="memory://x")
        [call] = self.calls()
        self.assertEqual((call["argv"], call["actor"], call["store"]), (["mcp"], "someone", "memory://x"))

    def test_without_ctx_it_is_a_server_with_no_tools(self):
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ctx_log"}}
        a, b, c = self.mcp(init, note, listing, call)
        self.assertEqual((a["id"], a["result"]["protocolVersion"]), (1, "2025-06-18"))
        self.assertIn("not installed", a["result"]["instructions"])
        self.assertEqual(b, {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}})
        self.assertEqual(c["error"]["code"], -32601)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_the_pinned_ctx_serves_the_write_tools(self):
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        replies = self.mcp(init, listing, KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        names = {t["name"] for t in replies[-1]["result"]["tools"]}
        self.assertTrue({"ctx_log", "ctx_str_replace", "ctx_insert", "ctx_fm", "ctx_create", "ctx_new", "ctx_move"} <= names)


class BashRoute(Base):
    def test_ctx_passes_every_argument_through_naming_the_store(self):
        r = self.adapter("ctx", "str_replace", "d/a", "--old", "x", "--new", "y", KIT_CTX=str(self.fake))
        self.assertEqual(r.returncode, 0, r.stderr)
        [call] = self.calls()
        self.assertEqual(call["argv"], ["--store", str(self.root), "str_replace", "d/a", "--old", "x", "--new", "y"])
        self.assertIsNone(call["actor"])  # ctx's own default: whoever runs it

    def test_ctx_exits_as_ctx_does_and_says_when_it_is_missing(self):
        self.assertEqual(self.adapter("ctx", "get", "x", KIT_CTX=str(self.fake), FAKE_CTX_RC="2").returncode, 2)
        r = self.adapter("ctx", "get", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not installed", r.stderr)


class McpJson(Base):
    def test_adds_the_server_and_keeps_everything_else(self):
        f = self.t / "ws" / ".mcp.json"
        r = self.adapter("mcp-json", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        entry = json.loads(f.read_text(encoding="utf-8"))["mcpServers"]["ctx"]
        self.assertEqual(entry, {"command": "python3", "args": [str(ADAPTER.resolve()), "mcp"]})
        f.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
        self.adapter("mcp-json", str(f))
        self.assertEqual(set(json.loads(f.read_text(encoding="utf-8"))["mcpServers"]), {"other", "ctx"})

    def test_never_replaces_an_existing_entry(self):
        f = self.t / ".mcp.json"
        mine = {"mcpServers": {"ctx": {"command": "/my/ctx", "args": ["mcp"]}}}
        f.write_text(json.dumps(mine), encoding="utf-8")
        r = self.adapter("mcp-json", str(f))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")), mine)

    def test_an_unreadable_file_is_exit_2_and_untouched(self):
        f = self.t / ".mcp.json"
        f.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.adapter("mcp-json", str(f)).returncode, 2)
        self.assertEqual(f.read_text(encoding="utf-8"), "{not json")


class CatalogRefresh(Base):
    """After a change under the content root the async hook regenerates INDEX.md and SESSION_INDEX.md, with or
    without ctx — the bookkeeping the skills no longer do."""

    def setUp(self):
        super().setUp()
        (self.root / "d").mkdir()
        (self.root / "d" / "a.md").write_text(EPIC, encoding="utf-8")

    def run_async(self, **kw) -> subprocess.CompletedProcess:
        env = kw.pop("env", {})
        r = self.adapter("hook", "post-tool-use-async", stdin=self.payload(**kw), **env)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        return r

    def catalogs(self) -> tuple[bool, bool]:
        return (self.root / "INDEX.md").exists(), (self.root / "SESSION_INDEX.md").exists()

    def test_a_write_under_the_root_refreshes_both_catalogs_without_ctx(self):
        self.run_async(tool_name="Write", tool_input={"file_path": str(self.root / "d" / "a.md")})
        self.assertEqual(self.catalogs(), (True, True))
        self.assertIn("d/a.md", (self.root / "INDEX.md").read_text(encoding="utf-8"))

    def test_a_ctx_tool_write_touches_and_refreshes(self):
        self.run_async(tool_name="mcp__plugin_ai-baton_ctx__ctx_log", tool_input={"doc": "d/a", "text": "x"},
                       env={"KIT_CTX": str(self.fake)})
        [call] = self.calls()
        self.assertEqual(call["argv"], ["--store", str(self.root), "touch", "--session", self.sid])
        self.assertEqual(self.catalogs(), (True, True))

    def test_a_ctx_read_or_a_write_elsewhere_refreshes_nothing(self):
        self.run_async(tool_name="mcp__ctx__ctx_get", tool_input={"doc": "d/a"}, env={"KIT_CTX": str(self.fake)})
        self.run_async(tool_name="Write", tool_input={"file_path": str(self.t / "elsewhere.md")})
        self.assertEqual((self.calls(), self.catalogs()), ([], (False, False)))

    def test_the_sync_hook_does_not_revalidate_a_ctx_tool_write(self):
        r = self.adapter("hook", "post-tool-use", KIT_CTX=str(self.fake),
                         stdin=self.payload(tool_name="mcp__ctx__ctx_str_replace", tool_input={"doc": "d/a"}))
        self.assertEqual((r.returncode, r.stdout, self.calls()), (0, "", []))

    def test_a_generator_failure_is_logged_not_shown(self):
        mod = load_adapter()
        def boom(*a, **k):
            raise OSError("no python")
        scratch = self.t / "scratch"
        with mock.patch.dict(os.environ, {"KIT_SCRATCH": str(scratch)}), mock.patch.object(mod.subprocess, "run", boom):
            mod.refresh_catalogs(self.root)
        log = (scratch / "hooks.log").read_text(encoding="utf-8")
        self.assertIn("gen_index.py: no python", log)
        self.assertIn("gen_sessions.py: no python", log)


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
