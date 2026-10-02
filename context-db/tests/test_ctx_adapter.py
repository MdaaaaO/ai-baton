"""ctx_adapter.py — the kit's adapter to ctx-store: one pin, a resolver that says "not installed" cleanly, a pinned
fetch, the Claude Code hooks (PreToolUse deny, PostToolUse validate/heartbeat/catalog refresh, SessionStart briefs)
that stay silent no-ops on a machine without ctx or without a store, `adopt` (the one-time store bootstrap), the MCP
server and the Bash route; plus `session register` stamping the harness session id a hook matches on.

Most tests run against a fake `ctx` (KIT_CTX) that logs its argv, so they need neither the real tool nor the network;
the end-to-end ones use the pinned install when this machine has it and skip cleanly otherwise. Every store, cache
and scratch path is a temp dir. Session ids and paths are assembled at run time. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import hashlib
import http.server
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
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
argv = sys.argv[1:]
settings = None
if "--settings" in argv:  # what `adopt` handed `init`, read at call time
    try:
        with open(argv[argv.index("--settings") + 1], encoding="utf-8") as sf:
            settings = sf.read()
    except OSError:
        pass
with open(os.environ["FAKE_CTX_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": argv, "store": os.environ.get("CTX_STORE"), "settings": settings,
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

    def wheel(self, reports: str, extra: dict[str, bytes] | None = None, main: bool = True) -> bytes:
        """A throw-away wheel shaped like ctx-store's — `ctxstore/__main__.py` printing `ctx <reports> (api 1)` —
        that `install()` downloads in these tests; never the network, never the real ctx-store."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ctxstore/__init__.py", "")
            if main:
                zf.writestr("ctxstore/__main__.py", f"print('ctx {reports} (api 1)')\n")
            zf.writestr("ctx_store-0.0.0.dist-info/METADATA", "Name: ctx-store\n")
            for name, data in (extra or {}).items():
                zf.writestr(name, data)
        return buf.getvalue()

    def pypi(self, version: str, wheel: bytes | None, filename: str = "") -> str:
        """Serve one release the way PyPI's JSON API does — `/pypi/ctx-store/<version>/json` listing the file, the
        file itself under `/files/` — from a local server for this test; returns the `index` template `install`
        takes. `wheel=None` serves no release at all (404). The JSON lists the served file's own digest, as PyPI's
        does: `install` must never take it as the pin."""
        files = self.served = {}
        if wheel is not None:
            filename = filename or f"ctx_store-{version}-py3-none-any.whl"
            files[f"/files/{filename}"] = wheel
            files[f"/pypi/ctx-store/{version}/json"] = json.dumps({"urls": [{
                "filename": filename, "packagetype": "bdist_wheel", "url": "{base}/files/" + filename,
                "digests": {"sha256": hashlib.sha256(wheel).hexdigest()}}]}).encode()
        self.requests: list[str] = []
        test = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — the stdlib's name
                test.requests.append(self.path)
                body = test.served.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                if self.path.endswith("/json"):
                    body = body.replace(b"{base}", test.base.encode())
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"
        return self.base + "/pypi/ctx-store/{version}/json"

    def local(self) -> None:
        """os.environ, for the rest of the test, set for an in-process `install()` against the local server: the
        hermetic env, and no proxy that could intercept a request to 127.0.0.1."""
        proxies = ("http_proxy", "https_proxy", "all_proxy")
        env = {k: v for k, v in hermetic_env(self.t).items() if k.lower() not in proxies}
        p = mock.patch.dict(os.environ, dict(env, NO_PROXY="*", no_proxy="*"))
        p.start()
        self.addCleanup(p.stop)
        for k in [k for k in os.environ if k.lower() in proxies]:
            del os.environ[k]

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]

    def payload(self, **kw) -> str:
        return json.dumps({"session_id": self.sid, "cwd": str(self.t), **kw})


class Pin(Base):
    def test_version_prints_the_one_pinned_release(self):
        mod = load_adapter()
        self.assertRegex(mod.CTX_VERSION, r"^\d+\.\d+\.\d+$")  # PyPI's version: no leading `v`
        self.assertRegex(mod.CTX_WHEEL_SHA256, r"^[0-9a-f]{64}$")
        r = self.adapter("version")
        lines = r.stdout.splitlines()
        self.assertEqual((r.returncode, lines[0]), (0, mod.CTX_VERSION))
        self.assertEqual(lines[1], f"api {mod.CTX_API}")
        self.assertEqual(lines[2], f"sha256 {mod.CTX_WHEEL_SHA256}")

    def test_the_tag_is_named_in_one_place(self):
        """No other kit file pins a ctx-store version of its own: a second copy would drift from CTX_VERSION."""
        pin = re.compile(r"ctx(?:-store)?\W{0,3}v?\d+\.\d+\.\d+|CTX_VERSION\s*=")
        hits = sorted(str(p.relative_to(KIT)) for p in KIT.rglob("*")
                      if p.is_file() and p.suffix in (".py", ".sh", ".md", ".json", ".mk", ".env", ".yml")
                      and not {".git", ".worktrees", "tests", "__pycache__"} & set(p.relative_to(KIT).parts)
                      and p.name != "CHANGELOG.md" and pin.search(p.read_text(encoding="utf-8", errors="replace")))
        self.assertEqual(hits, ["context-db/bin/ctx_adapter.py"])

    def test_the_pinned_location_carries_the_version(self):
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
    """The pinned fetch: PyPI's JSON names the release's py3-none-any wheel, the wheel's own sha256 must equal the
    pin, it is unzipped beside a `ctx` shim, checked against `ctx --version`, and renamed into place."""

    def setUp(self):
        super().setUp()
        self.mod = load_adapter()
        self.v = self.mod.CTX_VERSION
        self.dest = self.t / "cache" / "pinned"
        self.local()

    def install(self, wheel: bytes | None, sha256: str | None = None, **kw) -> Path:
        index = self.pypi(self.v, wheel, **kw)
        pin = hashlib.sha256(wheel).hexdigest() if sha256 is None and wheel is not None else (sha256 or "")
        return self.mod.install(version=self.v, sha256=pin, dest=self.dest, index=index)

    def assertNothingInstalled(self):
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.iterdir()), [])  # no temp dir left behind either

    def test_a_matching_digest_installs_a_working_ctx_and_is_recorded(self):
        wheel = self.wheel(self.v)
        self.assertEqual(self.install(wheel), self.dest)
        shim = self.dest / "ctx"
        self.assertTrue(os.access(shim, os.X_OK))
        r = subprocess.run([str(shim), "--version"], capture_output=True, text=True, timeout=30, cwd=self.t)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"ctx {self.v} (api 1)"))
        self.assertTrue((self.dest / "ctxstore" / "__main__.py").is_file())
        self.assertEqual(self.mod.pin_status(self.dest), {"version": self.v, "sha256": hashlib.sha256(wheel).hexdigest(),
                                                          "wheel": f"ctx_store-{self.v}-py3-none-any.whl"})
        self.assertEqual(sorted(p.name for p in self.dest.parent.iterdir()), ["pinned"])

    def test_the_shim_works_from_a_path_with_a_space_and_through_a_symlink(self):
        self.dest = self.t / "cache dir" / "pinned"
        self.install(self.wheel(self.v))
        link = self.t / "bin" / "ctx-link"
        link.symlink_to(self.dest / "ctx")
        for exe in (self.dest / "ctx", link):
            r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30, cwd=self.t)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, f"ctx {self.v} (api 1)"), r.stderr)

    def test_already_installed_is_returned_untouched_with_no_fetch(self):
        self.dest.mkdir(parents=True)
        (self.dest / "ctx").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        (self.dest / "ctx").chmod(0o755)
        self.assertEqual(self.install(self.wheel("9.9.9"), sha256="0" * 64), self.dest)
        self.assertEqual(self.requests, [])
        self.assertEqual((self.dest / "ctx").read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")
        self.assertIsNone(self.mod.pin_status(self.dest))  # no new record written either

    def test_a_mismatched_digest_installs_nothing_and_names_both(self):
        wheel = self.wheel(self.v)
        actual, wrong = hashlib.sha256(wheel).hexdigest(), "f" * 64
        with self.assertRaises(OSError) as cm:
            self.install(wheel, sha256=wrong)
        self.assertIn(actual, str(cm.exception))
        self.assertIn(wrong, str(cm.exception))
        self.assertNothingInstalled()

    def test_a_wheel_replaced_on_pypi_is_refused_though_its_json_lists_the_new_digest(self):
        """The pin recorded the original wheel; the index now serves another one and advertises that one's digest —
        the case the pin exists for: the JSON's digest is the server vouching for itself."""
        original, replaced = self.wheel(self.v), self.wheel(self.v, extra={"ctxstore/evil.py": b"x = 1\n"})
        self.assertNotEqual(hashlib.sha256(original).digest(), hashlib.sha256(replaced).digest())
        with self.assertRaises(OSError) as cm:
            self.install(replaced, sha256=hashlib.sha256(original).hexdigest())
        self.assertIn(hashlib.sha256(replaced).hexdigest(), str(cm.exception))
        self.assertNothingInstalled()

    def test_zip_slip_members_are_refused_before_anything_is_written(self):
        for bad in ("../escaped.py", "/abs/escaped.py", "ctxstore/../../escaped.py"):
            with self.subTest(member=bad):
                wheel = self.wheel(self.v, extra={bad: b"x = 1\n"})
                with self.assertRaisesRegex(OSError, "absolute or escapes"):
                    self.install(wheel)
                self.assertNothingInstalled()
                self.assertFalse((self.t / "cache" / "escaped.py").exists())
                self.assertFalse((self.t / "escaped.py").exists())

    def test_no_py3_none_any_wheel_is_a_clear_error(self):
        with self.assertRaisesRegex(OSError, "lists no py3-none-any.whl wheel"):
            self.install(self.wheel(self.v), filename=f"ctx_store-{self.v}-cp312-cp312-linux_x86_64.whl")
        self.assertNothingInstalled()

    def test_a_release_pypi_does_not_have_is_an_error_not_a_half_copy(self):
        with self.assertRaisesRegex(OSError, "404"):
            self.install(None, sha256="0" * 64)
        self.assertNothingInstalled()

    def test_a_wheel_reporting_another_version_is_refused(self):
        with self.assertRaisesRegex(OSError, "0.0.1"):
            self.install(self.wheel("0.0.1"))
        self.assertNothingInstalled()

    def test_a_wheel_without_ctxstore_is_refused(self):
        with self.assertRaisesRegex(OSError, "not a ctx-store wheel"):
            self.install(self.wheel(self.v, main=False))
        self.assertNothingInstalled()

    def test_no_digest_pinned_refuses_without_fetching(self):
        with self.assertRaisesRegex(OSError, "no wheel digest is pinned"):
            self.install(self.wheel(self.v), sha256="")
        self.assertEqual(self.requests, [])
        self.assertFalse(self.dest.exists())

    def test_a_stalled_download_times_out(self):
        # setup.sh fetches unattended: a black-holed network must fail, never hang — a socket that accepts the
        # connection and never answers
        self.mod.INSTALL_TIMEOUT = 1
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)
        index = f"http://127.0.0.1:{sock.getsockname()[1]}/pypi/ctx-store/{{version}}/json"
        start = time.monotonic()
        with self.assertRaisesRegex(OSError, "did not finish within 1s"):
            self.mod.install(version=self.v, sha256="0" * 64, dest=self.dest, index=index)
        self.assertLess(time.monotonic() - start, 15)
        self.assertNothingInstalled()

    def test_a_concurrent_install_that_wins_the_rename_stands(self):
        real = self.mod._write_pin_status

        def and_then_another_install_lands(dest, **kw):
            real(dest, **kw)
            self.dest.mkdir(parents=True)  # the other run's rename lands first
            (self.dest / "ctx").write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
            (self.dest / "ctx").chmod(0o755)
        self.mod._write_pin_status = and_then_another_install_lands
        self.assertEqual(self.install(self.wheel(self.v)), self.dest)
        self.assertEqual((self.dest / "ctx").read_text(encoding="utf-8"), "#!/bin/sh\necho theirs\n")
        self.assertEqual(sorted(p.name for p in self.dest.parent.iterdir()), ["pinned"])

    def test_pin_status_is_none_before_any_install(self):
        self.assertIsNone(self.mod.pin_status(self.t / "cache" / "nowhere"))

    def test_a_record_without_a_wheel_digest_reads_as_none(self):
        self.dest.mkdir(parents=True)  # what a clone-era install left: no `sha256` key
        (self.dest / self.mod.PIN_STATUS_NAME).write_text(json.dumps(
            {"pinned_sha": "a" * 40, "cloned_sha": "a" * 40, "verified": True}), encoding="utf-8")
        self.assertIsNone(self.mod.pin_status(self.dest))

    def test_the_pin_cli_reports_the_record(self):
        wheel = self.wheel(self.v)
        index = self.pypi(self.v, wheel)
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.t / "cache")}):
            self.mod.install(version=self.v, sha256=hashlib.sha256(wheel).hexdigest(), index=index)
        r = self.adapter("pin", XDG_CACHE_HOME=str(self.t / "cache"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"version {self.v}\nwheel ctx_store-{self.v}-py3-none-any.whl\n"
                                   f"sha256 {hashlib.sha256(wheel).hexdigest()}\n")

    def test_the_pin_cli_is_exit_1_when_nothing_is_installed(self):
        r = self.adapter("pin", XDG_CACHE_HOME=str(self.t / "cache"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("ctx_adapter.py install", r.stderr)
        self.assertIn("remove that directory first", r.stderr)  # `install` keeps a copy that is already there


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
        self.assertEqual((r.returncode, r.stdout), (0, "compacted — re-grounded from doc and no context doc\ndoc\n"))
        [call] = self.calls()  # no `epic:` field on the fake's one-line answer: no second (resolve/find) call
        self.assertEqual(call["argv"][:5], ["--store", str(self.root), "brief", "--session", self.sid])
        self.assertIn("--full", call["argv"])
        self.assertIn("--budget", call["argv"])

    def test_a_failed_brief_prints_nothing(self):
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), FAKE_CTX_RC="2",
                         FAKE_CTX_OUT="partial", FAKE_CTX_ERR="NO_SUCH_DOC x: no such doc\n")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_a_long_first_priority_section_still_shows_the_head_of_the_others(self):
        brief = (
            "sessions/lane-topic (session, 500 bytes)\nsession: lane-topic\n\n"
            "# Session: lane-topic\n\n<!-- What this session is doing right now, what it OWNS, and what\n"
            "     another session must coordinate with it on. Override this whenever that changes. -->\n\n"
            "## Open PRs\n\n" + "\n\n".join(
                f"acme/widgets#{i} — waits on a flaky nightly test unrelated to this change, not a real failure"
                for i in range(1, 60)
            ) + "\n\n"
            "## Open decisions\n\n- keep the retry logic in the adapter, not ctx-store\n- revisit the lock timeout later\n\n"
            "## Assumptions\n\n- ctx is already installed on CI\n- no one edits sessions/ by hand\n\n"
            "## Owns\n\n- the lane end to end\n")
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), FAKE_CTX_OUT=brief)
        self.assertEqual(r.returncode, 0)
        out = r.stdout
        self.assertIn("## Open decisions", out)
        self.assertIn("- keep the retry logic in the adapter, not ctx-store", out)
        self.assertIn("## Assumptions", out)
        self.assertIn("- ctx is already installed on CI", out)
        self.assertIn("## Owns", out)
        self.assertIn("- the lane end to end", out)
        # the long first section's own cut names the session doc, not a budget flag its reader cannot raise
        self.assertIn("more lines — read sessions/lane-topic", out)
        self.assertNotIn("raise --budget", out)
        mod = load_adapter()
        owner_bytes = len(out.splitlines()[0].encode("utf-8")) + 1
        self.assertLessEqual(len(out.encode("utf-8")) - owner_bytes, mod.BRIEF_BUDGET)

    def test_a_short_session_prints_whole_minus_the_preamble(self):
        brief = ("sessions/lane-topic (session, 80 bytes)\nsession: lane-topic\n\n"
                 "# Session: lane-topic\n\n<!-- override this whenever responsibilities change -->\n\n"
                 "## Notes\n\nshort note\n")
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), FAKE_CTX_OUT=brief)
        self.assertEqual(r.returncode, 0)
        out = r.stdout
        self.assertIn("## Notes", out)
        self.assertIn("short note", out)
        self.assertNotIn("# Session: lane-topic", out)
        self.assertNotIn("<!--", out)

    def test_a_preamble_with_real_text_is_kept(self):
        brief = ("sessions/lane-topic (session, 80 bytes)\nsession: lane-topic\n\n"
                 "# Session: lane-topic\n\nSomething this session actually wrote above its first heading.\n\n"
                 "## Notes\n\nshort note\n")
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), FAKE_CTX_OUT=brief)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Something this session actually wrote above its first heading.", r.stdout)


class CompactBriefHelpers(unittest.TestCase):
    """The compact brief's own post-processing of a `ctx brief --session` answer — pure functions, no store, no
    ctx: ctx's own `brief --budget` has no way to drop frontmatter keys or reorder `##` sections (`ctx help
    brief`), so the adapter re-budgets ctx's full answer itself (`_compact_brief`, below)."""

    def test_fit_lines_keeps_everything_that_fits(self):
        mod = load_adapter()
        lines = ["a", "b", "c"]
        self.assertEqual(mod._fit_lines(lines, 4096), lines)

    def test_fit_lines_adds_the_ctx_tail_marker_and_stays_within_budget(self):
        mod = load_adapter()
        lines = [f"line {i}" for i in range(50)]
        kept = mod._fit_lines(lines, 80)
        self.assertTrue(kept[-1].startswith("… "), kept[-1])
        self.assertIn("more lines, raise --budget", kept[-1])
        self.assertLessEqual(sum(len(ln.encode("utf-8")) + 1 for ln in kept), 80)

    def test_split_brief_doc_separates_frontmatter_sections_line_and_body(self):
        mod = load_adapter()
        raw = ("sessions/foo (session, 10 bytes)\nsession: foo\nstats: turns 1\nheartbeat: 2026-01-01T00:00:00Z\n"
               "sections: Notes (5)\n\n# Session: foo\n\n## Notes\nhi\n")
        header, fm, body = mod._split_brief_doc(raw)
        self.assertEqual(header, "sessions/foo (session, 10 bytes)")
        self.assertEqual(fm, {"session": "foo", "stats": "turns 1", "heartbeat": "2026-01-01T00:00:00Z",
                              "sections": "Notes (5)"})
        self.assertEqual(body, "# Session: foo\n\n## Notes\nhi")

    def test_split_brief_doc_on_empty_input(self):
        mod = load_adapter()
        self.assertEqual(mod._split_brief_doc(""), ("", {}, ""))

    def test_filtered_frontmatter_keeps_only_the_named_fields_in_order(self):
        mod = load_adapter()
        fm = {"session": "foo", "session_id": "sid", "ref": "r1", "status": "active", "epic": "acme/widgets#42",
              "repos": "acme/widgets", "working_on": "x", "responsibilities": "y" * 200, "stats": "turns 1",
              "heartbeat": "2026-01-01T00:00:00Z", "updated": "2026-01-01"}
        out = mod._filtered_frontmatter(fm)
        self.assertEqual([ln.split(":", 1)[0] for ln in out], ["session", "epic", "working_on", "responsibilities"])
        kept_resp = out[-1][len("responsibilities: "):]
        self.assertEqual(len(kept_resp), mod.RESPONSIBILITIES_MAX)
        self.assertEqual(kept_resp, "y" * mod.RESPONSIBILITIES_MAX)

    def test_filtered_frontmatter_on_a_bare_session(self):
        mod = load_adapter()
        self.assertEqual(mod._filtered_frontmatter({"session": "foo", "status": "active"}), ["session: foo"])

    def test_reorder_sections_leads_with_the_priority_list_in_order(self):
        mod = load_adapter()
        body = "# T\n\n## Notes\nn\n\n## Owns\no\n\n## Open PRs\np\n\n## Worktrees\nw\n"
        out = mod._reorder_sections(body)
        self.assertEqual(re.findall(r"(?m)^## (.+)$", out), ["Open PRs", "Owns", "Worktrees", "Notes"])
        self.assertTrue(out.startswith("# T\n\n"))

    def test_reorder_sections_without_any_heading_is_unchanged(self):
        mod = load_adapter()
        self.assertEqual(mod._reorder_sections("just a title\n"), "just a title\n")

    def test_reorder_sections_keeps_non_priority_order_stable(self):
        mod = load_adapter()
        body = "## Zebra\nz\n\n## Apple\na\n\n## Open PRs\np\n"
        out = mod._reorder_sections(body)
        self.assertEqual(re.findall(r"(?m)^## (.+)$", out), ["Open PRs", "Zebra", "Apple"])

    def test_fit_lines_names_the_doc_instead_of_raise_budget_when_given_one(self):
        mod = load_adapter()
        lines = [f"line {i}" for i in range(50)]
        kept = mod._fit_lines(lines, 80, "sessions/lane-topic")
        self.assertTrue(kept[-1].startswith("… "), kept[-1])
        self.assertIn("more lines — read sessions/lane-topic", kept[-1])
        self.assertNotIn("raise --budget", kept[-1])
        self.assertLessEqual(sum(len(ln.encode("utf-8")) + 1 for ln in kept), 80)

    def test_strip_preamble_drops_the_title_and_html_comment(self):
        mod = load_adapter()
        body = "# Session: foo\n\n<!-- override this\n     whenever responsibilities change -->\n\n## Notes\nhi\n"
        self.assertEqual(mod._strip_preamble(body), "## Notes\nhi\n")

    def test_strip_preamble_keeps_real_text(self):
        mod = load_adapter()
        body = "# Session: foo\n\nA note the session actually wrote here.\n\n## Notes\nhi\n"
        self.assertEqual(mod._strip_preamble(body), body)

    def test_strip_preamble_keeps_a_deeper_heading_above_the_first_section(self):
        mod = load_adapter()
        body = "# Session: foo\n\n### blocker: the store is read-only\n\n## Notes\nhi\n"
        self.assertEqual(mod._strip_preamble(body), body)

    def test_strip_preamble_without_any_heading_is_unchanged(self):
        mod = load_adapter()
        self.assertEqual(mod._strip_preamble("just a title\n"), "just a title\n")

    def test_fit_sections_gives_every_priority_section_its_head_before_any_gets_more(self):
        mod = load_adapter()
        body = ("## Open PRs\n\n" + "\n".join(f"- pr {i}" for i in range(1, 20)) + "\n\n"
                "## Open decisions\n\n- dec 1\n- dec 2\n\n"
                "## Assumptions\n\n- assume 1\n")
        out = mod._fit_sections(body, 140, "sessions/lane-topic")
        text = "\n".join(out)
        self.assertIn("## Open decisions", text)
        self.assertIn("- dec 1", text)
        self.assertIn("## Assumptions", text)
        self.assertIn("- assume 1", text)
        self.assertIn("more lines — read sessions/lane-topic", text)
        self.assertNotIn("- pr 19", text)  # the long first section's tail did not all fit

    def test_fit_sections_stays_within_the_budget_when_every_priority_section_is_cut(self):
        mod = load_adapter()
        body = "".join(f"## {name}\n\n" + "\n".join(f"- {name} line {i}" for i in range(1, 30)) + "\n\n"
                       for name in mod.SECTION_PRIORITY) + "## Notes\n\n" + "\n".join(["a note"] * 20) + "\n"
        budget = 900
        out = mod._fit_sections(body, budget, "sessions/lane-topic")
        self.assertLessEqual(sum(len(line.encode("utf-8")) + 1 for line in out), budget)
        text = "\n".join(out)
        for name in mod.SECTION_PRIORITY:
            self.assertIn(f"- {name} line {mod.SECTION_HEAD_LINES}", text)
        self.assertEqual(text.count("more lines — read sessions/lane-topic"), len(mod.SECTION_PRIORITY) + 1)

    def test_fit_sections_prints_a_short_body_whole(self):
        mod = load_adapter()
        body = "## Notes\n\nhi\n"
        self.assertEqual(mod._fit_sections(body, 2048, "sessions/lane-topic"), body.split("\n"))


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
# the optional section (#389): not in the epic type's required list, so a doc either carries it or not
EPIC_WITH_ARCHITECTURE = EPIC.replace(
    "## Remaining work",
    "## Architecture\n\n- `widget` [service] (existing)\n  - → `queue` [service] (existing): enqueues a job\n\n"
    "## Remaining work")


class StoreData(unittest.TestCase):
    """The kit ships the store settings and type schemas `adopt` writes: data the deny and the catalogs agree with."""

    def test_settings_and_schemas_parse(self):
        data = KIT / "context-db" / "ctx-store"
        settings = json.loads((data / "ctx-store.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["schema_version"], 1)
        self.assertTrue({"INDEX.md", "SESSION_INDEX.md"} <= set(settings["generated"]))  # the kit's generators write them
        self.assertIn("memory/**", settings["ignore"])  # the harness auto-memory is never a store doc
        # every MCP write names its own actor (the caller's registered session name), checked in full against this
        # pattern — the same one session.py's own NAME_RE enforces on a session name, so the two never drift apart.
        sys.path.insert(0, str(BIN))
        import session  # noqa: E402  — same dir
        self.assertEqual(settings["mcp"]["actors"], session.NAME_RE.pattern)
        self.assertEqual(settings["maintain"]["keep_log"], 6)  # session-handoff's "~6 max" Session-log cap
        types = sorted(p.stem for p in (data / "types").glob("*.json"))
        self.assertTrue({"epic", "session", "ledger", "log", "self-assessment"} <= set(types), types)
        for t in types:
            self.assertIsInstance(json.loads((data / "types" / f"{t}.json").read_text(encoding="utf-8")), dict, t)
        epic = json.loads((data / "types" / "epic.json").read_text(encoding="utf-8"))
        # the Session log reads oldest first; version 1 migrates a newest-first log, one-date logs included
        self.assertEqual(epic["log"], {"section": "Session log", "order": "oldest-first"})
        self.assertEqual(epic["version"], 1)
        self.assertEqual(epic["migrations"][0]["log_order_from"], "newest-first")
        self.assertNotIn("Architecture", epic["sections"])  # optional (#389): the template has it, the type does not
        log = json.loads((data / "types" / "log.json").read_text(encoding="utf-8"))
        # no section: `ctx log`/`maintain`'s archive moves work the body-level dated list, newest entry first
        self.assertEqual(log["log"], {"order": "newest-first"})
        session = json.loads((data / "types" / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(session["owner"], "session")  # back since v0.6.0 (#356): a per-call MCP actor names the owner

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
        calls = self.calls()
        self.assertEqual([c["argv"][2:] for c in calls],
                         [["init", "--settings", str(data / "ctx-store.json"), "--types", str(data / "types"),
                           "--upgrade"],
                          ["migrate", "--apply"], ["validate"], ["validate", "--changed", "--adopt"]])
        self.assertEqual({c["argv"][1] for c in calls}, {str(self.root)})
        # the kit's own settings file, `mcp` included: `init` takes every key itself, the kit edits none of them
        self.assertIn("mcp", json.loads(calls[0]["settings"]))
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

    def marker(self) -> dict:
        return json.loads((self.root / "ctx-store.json").read_text(encoding="utf-8"))

    def kit_settings(self) -> dict:
        return json.loads((KIT / "context-db" / "ctx-store" / "ctx-store.json").read_text(encoding="utf-8"))

    def adopt_with_settings(self, settings: dict, *args: str) -> tuple[int, str]:
        """`adopt` in-process against a copy of the kit's store data whose settings are `settings` — a kit release
        that changed them, without touching the repo's own copy."""
        import contextlib
        import shutil
        changed = self.t / f"kit-data-{uuid.uuid4().hex[:8]}"
        shutil.copytree(KIT / "context-db" / "ctx-store", changed)
        (changed / "ctx-store.json").write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n",
                                                 encoding="utf-8")
        mod = load_adapter()
        mod.STORE_DATA = changed
        out = io.StringIO()
        env = {"KIT_CTX": str(REAL_CTX), "CTX_NO_WALK": "1", "CONTEXT_ROOT": str(self.root)}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            rc = mod.adopt(replace="--replace" in args)
        return rc, out.getvalue()

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_init_writes_mcp_and_a_kit_settings_change_reaches_the_store(self):
        """The kit's whole settings file, `mcp` included, goes through `ctx init` — the marker carries it with no
        kit-side write, so a later kit settings change is an upgrade `init` itself recognises, not a hand edit."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        self.assertEqual(self.marker()["mcp"], self.kit_settings()["mcp"])
        settings = self.kit_settings()
        settings["maintain"]["keep_log"] = 5  # a kit settings change with nothing to do with mcp
        rc, out = self.adopt_with_settings(settings)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("differs:", out)
        self.assertEqual(self.marker()["maintain"], {"keep_log": 5})
        self.assertEqual(self.marker()["mcp"], settings["mcp"])

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_a_hand_edited_marker_reports_kept_and_keeps_mcp(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        data = self.marker()
        data["maintain"]["keep_log"] = 99  # a hand edit to a real value
        (self.root / "ctx-store.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("differs: ctx-store.json", r.stdout)
        self.assertIn("adopt --replace", r.stdout)
        self.assertEqual(self.marker()["maintain"], {"keep_log": 99})
        self.assertEqual(self.marker()["mcp"], self.kit_settings()["mcp"])

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_a_differing_mcp_is_kept_like_any_edit_until_replace(self):
        """`mcp` follows the same kept rule as every other key: a local edit is reported (exit 5), never
        overwritten behind the user's back, and `adopt --replace` takes the kit's."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        data = self.marker()
        data["mcp"] = {"actors": "^nobody-writes-through-this-pattern$"}  # a local edit
        (self.root / "ctx-store.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("differs: ctx-store.json", r.stdout)
        self.assertEqual(self.marker()["mcp"], data["mcp"])
        r = self.adapter("adopt", "--replace", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.marker()["mcp"], self.kit_settings()["mcp"])

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_an_unchanged_mcp_prints_nothing(self):
        """The ordinary idempotent case: a second `adopt` reports nothing about the marker."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("differs:", r.stdout)
        self.assertNotIn("updated:", r.stdout)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_an_unparsable_marker_is_left_untouched_and_reported(self):
        """A marker that does not parse is never skipped silently and never rewritten: `ctx` reports it (exit 3,
        a finding) and the file is left exactly as broken as it was found."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        broken = '{"mcp": {"actors": "x"'  # truncated — not valid JSON
        (self.root / "ctx-store.json").write_text(broken, encoding="utf-8")
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertRegex(r.stdout + r.stderr, r"SCHEMA_VIOLATION ctx-store\.json")
        self.assertEqual((self.root / "ctx-store.json").read_text(encoding="utf-8"), broken)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_a_store_an_older_kit_mcp_patched_adopts_clean(self):
        """Migration: an older kit ran `init` without `mcp`, then wrote `mcp` into the marker itself. The new kit's
        `adopt` finds the marker equal to its settings (exit 0, nothing differs); `init`'s record of the marker
        predates that write, so the first kit settings change after it reports the marker kept, once — `adopt
        --replace` takes the kit's, and the record lines up from then on (the module docstring's note)."""
        old_settings = self.kit_settings()
        mcp = old_settings.pop("mcp")
        old = self.t / "old-kit-settings.json"
        old.write_text(json.dumps(old_settings), encoding="utf-8")
        subprocess.run([str(REAL_CTX), "--store", str(self.root), "init", "--settings", str(old), "--types",
                        str(KIT / "context-db" / "ctx-store" / "types")], check=True, capture_output=True,
                       env=dict(os.environ, CTX_NO_WALK="1"))
        patched = dict(self.marker(), mcp=mcp)  # what the older kit's own write left
        (self.root / "ctx-store.json").write_text(json.dumps(patched, indent=2) + "\n", encoding="utf-8")
        r = self.adapter("adopt", KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("differs:", r.stdout)
        settings = self.kit_settings()
        settings["maintain"]["keep_log"] = 5
        rc, out = self.adopt_with_settings(settings)
        self.assertEqual(rc, 5, out)  # the once
        self.assertIn("differs: ctx-store.json", out)
        rc, out = self.adopt_with_settings(settings, "--replace")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.marker()["maintain"], {"keep_log": 5})
        settings["maintain"]["keep_log"] = 7
        rc, out = self.adopt_with_settings(settings)
        self.assertEqual(rc, 0, out)  # lined up: the next change goes straight through
        self.assertEqual(self.marker()["maintain"], {"keep_log": 7})

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_adopt_migrates_a_newest_first_session_log_to_chronological(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        (self.root / "d").mkdir()
        body = EPIC.replace("- 2026-01-05 — first\n", "- 2026-01-06 — later\n- 2026-01-06 — same day, earlier\n"
                            "- 2026-01-05 — first\n")
        (self.root / "d" / "a.md").write_text(body, encoding="utf-8")
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("migrated:", r.stdout)
        text = (self.root / "d" / "a.md").read_text(encoding="utf-8")
        log = [ln for ln in text.splitlines() if ln.startswith("- 2026-")]
        self.assertEqual(log, ["- 2026-01-05 — first", "- 2026-01-06 — same day, earlier", "- 2026-01-06 — later"])
        self.assertIn("schema_version: epic.v1", text)
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)  # a second run changes nothing
        self.assertEqual((self.root / "d" / "a.md").read_text(encoding="utf-8"), text)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_the_model_can_write_its_own_session_doc_by_naming_its_actor(self):
        """The session type's owner rule (back since v0.6.0, #356): a write to a session's own file succeeds
        when it names that session as the actor — the Bash fallback reads CTX_ACTOR, not a default identity."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        (self.root / "sessions").mkdir(exist_ok=True)
        (self.root / "sessions" / "lane-topic.md").write_text(
            "---\nsession: lane-topic\nstatus: active\n---\n\n## Owns\n\n- x\n", encoding="utf-8")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        r = self.adapter("ctx", "insert", "sessions/lane-topic", "- y", "--line", "7", **dict(env, CTX_ACTOR="lane-topic"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NOT_OWNER", r.stdout + r.stderr)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_findings_are_printed_and_exit_3(self):
        (self.root / "d").mkdir()
        (self.root / "d" / "bad.md").write_text(EPIC.replace("status: active", "status: someday"), encoding="utf-8")
        r = self.adapter("adopt", KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        # an invalid epic cannot take the schema step: validate names the doc, migrate names the violation
        self.assertRegex(r.stdout, r"(?m)^finding: MIGRATION_PENDING d/bad")
        self.assertRegex(r.stdout, r"(?m)^finding: migrate: SCHEMA_VIOLATION d/bad status")  # the doc that
        # blocks the step, not just the field
        self.assertTrue((self.root / "ctx-store.json").exists())

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_the_optional_architecture_section_is_not_required_either_way(self):
        """`## Architecture` lives in the template but not in the epic type's required `sections` list
        (#389) — an epic doc without it, and one with it, both validate clean."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        (self.root / "d").mkdir()
        (self.root / "d" / "bare.md").write_text(EPIC, encoding="utf-8")
        (self.root / "d" / "drawn.md").write_text(EPIC_WITH_ARCHITECTURE, encoding="utf-8")
        r = self.adapter("adopt", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("finding:", r.stdout)
        self.assertIn("2 adopted", r.stdout)

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

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_maintain_archives_the_oldest_session_log_entries_newest_first(self):
        """The kit's own `maintain.keep_log` (6, session-handoff's "~6 max") and the `log` type's body-level,
        newest-first order (#393): an oversized epic keeps its newest entries, oldest-first, and the rest move
        to the archive doc `maintain` names by default (`archive/{slug}-log`), newest-first, no `## Log` section.
        No archive doc exists beforehand: `maintain` creates it with every field the `log` type requires, so the
        store still validates clean after it."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        (self.root / "d").mkdir()
        # 8 entries, oldest-first, padded well past the 30KB size guard so `maintain` actually trims this doc.
        entries = [f"- 2026-01-{day:02d} — entry {day} " + "z" * 4000 for day in range(1, 9)]
        body = ("---\ntitle: A\ntype: epic\ndomain: d\nstatus: active\nupdated: 2026-01-08\n---\n# A\n\n"
                "## Goal\n\ng\n\n## Key decisions & gotchas\n\n## Remaining work\n\n## Session log\n\n"
                + "\n".join(entries) + "\n")
        (self.root / "d" / "a.md").write_text(body, encoding="utf-8")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        self.assertFalse((self.root / "archive" / "a-log.md").exists())
        r = self.adapter("ctx", "maintain", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("archived: 2 log entries of d/a", r.stdout)
        kept = [ln for ln in (self.root / "d" / "a.md").read_text(encoding="utf-8").splitlines()
                if ln.startswith("- 2026-")]
        self.assertEqual(kept, entries[2:])  # the newest 6 stay, oldest-first, as the Session log already reads
        archive = self.root / "archive" / "a-log.md"
        self.assertTrue(archive.is_file())
        archived = [ln for ln in archive.read_text(encoding="utf-8").splitlines() if ln.startswith("- 2026-")]
        self.assertEqual(archived, list(reversed(entries[:2])))  # the 2 oldest, moved newest-first
        self.assertNotIn("## Log", archive.read_text(encoding="utf-8"))  # no section: the body-level dated list
        self.assertEqual(self.adapter("ctx", "maintain", **env).returncode, 0)  # a second run changes nothing
        r = self.adapter("ctx", "validate", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # the archive doc `maintain` made validates too


class AdoptBehind(Base):
    """A full `adopt` records a digest of the kit's store settings + type schemas under the store's ignored
    `state/` dir; `adopt --check` recomputes it and reports the store `behind`, exit 6, on any mismatch —
    including no record at all (a store adopted before this existed): the least surprising default, since one
    plain `adopt` clears it either way. Findings (exit 3) still win over `behind` — the existing `--check`
    precedence is untouched."""

    def digest_path(self) -> Path:
        return self.root / "state" / "ctx-adapter" / "adopted-kit.json"

    def test_adopt_records_a_digest_check_is_clean_then_behind_with_no_record(self):
        self.assertEqual(self.adapter("adopt", KIT_CTX=str(self.fake), FAKE_CTX_OUT="ok: 0 docs checked\n")
                          .returncode, 0)
        self.assertTrue(self.digest_path().is_file())
        self.assertEqual(self.adapter("adopt", "--check", KIT_CTX=str(self.fake)).returncode, 0)
        self.digest_path().unlink()  # a store adopted before this digest existed carries no record at all
        r = self.adapter("adopt", "--check", KIT_CTX=str(self.fake))
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("behind", r.stdout)

    def test_check_never_writes_the_digest(self):
        r = self.adapter("adopt", "--check", KIT_CTX=str(self.fake))
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)  # adopted (no NO_STORE), never recorded: behind
        self.assertFalse(self.digest_path().exists())

    def test_findings_still_win_over_behind(self):
        r = self.adapter("adopt", "--check", KIT_CTX=str(self.fake), FAKE_CTX_RC="3",
                          FAKE_CTX_ERR="SCHEMA_VIOLATION d/bad status\n")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)  # not 6, though nothing was ever recorded either

    def test_a_type_edited_in_a_copy_of_the_kit_data_is_behind_then_clean_after_adopt(self):
        """The issue's own proof: adopt once, change a type in a COPY of the kit's types (never the repo's own
        copy), `--check` reports behind, adopt again clears it."""
        import shutil
        mod = load_adapter()
        env = {**self.env, "KIT_CTX": str(self.fake), "FAKE_CTX_OUT": "ok: 0 docs checked\n"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mod.adopt(), 0)
            self.assertEqual(mod.adopt(check=True), 0)
            changed = self.t / "changed-kit-data"
            shutil.copytree(KIT / "context-db" / "ctx-store", changed)
            data = json.loads((changed / "types" / "log.json").read_text(encoding="utf-8"))
            data["frontmatter"]["title"]["required"] = not data["frontmatter"]["title"]["required"]
            (changed / "types" / "log.json").write_text(json.dumps(data), encoding="utf-8")
            mod.STORE_DATA = changed
            self.assertEqual(mod.adopt(check=True), 6)
            self.assertEqual(mod.adopt(), 0)  # a plain adopt (still against `changed`) records a fresh digest
            self.assertEqual(mod.adopt(check=True), 0)  # … so --check against the same `changed` data is clean

    def test_a_kept_file_still_differs_in_check_even_though_the_digest_matches(self):
        """A digest match alone used to read as clean — but a file `ctx init --upgrade` kept (edited here) at the
        last full adopt is still a local edit `--check` must keep naming, not hide behind an unmoved digest."""
        r = self.adapter("adopt", KIT_CTX=str(self.fake), FAKE_CTX_OUT="kept: .ctx/types/epic.json\nok: 1 adopted\n")
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("differs: .ctx/types/epic.json — kept (edited here); `ctx_adapter.py adopt --replace` "
                      "takes the kit's", r.stdout)
        recorded = json.loads(self.digest_path().read_text(encoding="utf-8"))
        self.assertEqual(recorded["kept"], [".ctx/types/epic.json"])
        r = self.adapter("adopt", "--check", KIT_CTX=str(self.fake))  # the digest did not move: still 0 by itself
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)  # but the recorded kept file still differs
        self.assertIn("differs: .ctx/types/epic.json — kept (edited here); `ctx_adapter.py adopt --replace` "
                      "takes the kit's", r.stdout)
        self.assertEqual(json.loads(self.digest_path().read_text(encoding="utf-8")), recorded)  # --check wrote nothing

    def test_a_newer_recorded_kit_version_reads_as_ahead_not_behind(self):
        """Version skew must not point `adopt` the wrong way: a store last adopted by a NEWER kit than this one is
        not `behind` — this kit is the one out of date, so `--check` says `ahead` and exits 0, never `adopt`'s
        own advice (which would hand the store older types)."""
        mod = load_adapter()
        env = {**self.env, "KIT_CTX": str(self.fake), "FAKE_CTX_OUT": "ok: 0 docs checked\n"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mod.adopt(), 0)
            state = json.loads(self.digest_path().read_text(encoding="utf-8"))
            state["digest"] = "0" * 64  # also stale, so this is a genuine mismatch, not just a version bump
            state["version"] = "99.0.0"  # newer than any real kit release
            self.digest_path().write_text(json.dumps(state), encoding="utf-8")
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = mod.adopt(check=True)
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("ahead: the store was adopted by kit 99.0.0", out.getvalue())
            self.assertNotIn("behind", out.getvalue())

    def test_version_skew_compares_numerically_not_lexically(self):
        """`0.10.0` sorts before `0.9.0` as a string — the comparison must be numeric or this reads backwards."""
        mod = load_adapter()
        mod._kit_version = lambda: "0.9.0"  # this test's own stand-in for "this kit's release"
        env = {**self.env, "KIT_CTX": str(self.fake), "FAKE_CTX_OUT": "ok: 0 docs checked\n"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mod.adopt(), 0)
            state = json.loads(self.digest_path().read_text(encoding="utf-8"))
            self.assertEqual(state["version"], "0.9.0")
            state["digest"] = "0" * 64
            state["version"] = "0.10.0"
            self.digest_path().write_text(json.dumps(state), encoding="utf-8")
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = mod.adopt(check=True)
            self.assertEqual(rc, 0, out.getvalue())  # numerically 0.10.0 > 0.9.0: ahead, not behind
            self.assertIn("ahead: the store was adopted by kit 0.10.0", out.getvalue())

    def test_an_older_recorded_version_still_reads_as_behind(self):
        mod = load_adapter()
        env = {**self.env, "KIT_CTX": str(self.fake), "FAKE_CTX_OUT": "ok: 0 docs checked\n"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mod.adopt(), 0)
            state = json.loads(self.digest_path().read_text(encoding="utf-8"))
            state["digest"] = "0" * 64
            state["version"] = "0.1.0"  # older than this kit's own real version
            self.digest_path().write_text(json.dumps(state), encoding="utf-8")
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = mod.adopt(check=True)
            self.assertEqual(rc, 6, out.getvalue())
            self.assertIn("behind:", out.getvalue())

    def test_a_newer_recorded_version_refuses_a_plain_adopt_until_replace(self):
        """Plain `adopt` must not silently hand an older kit's types to a store a newer kit already adopted —
        ctx-store's own `init --upgrade` has no guard for that (see the module docstring's `Behind` paragraph)."""
        mod = load_adapter()
        env = {**self.env, "KIT_CTX": str(self.fake), "FAKE_CTX_OUT": "ok: 0 docs checked\n"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mod.adopt(), 0)
            state = json.loads(self.digest_path().read_text(encoding="utf-8"))
            state["version"] = "99.0.0"
            self.digest_path().write_text(json.dumps(state), encoding="utf-8")
            import contextlib
            import io
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = mod.adopt()
            self.assertEqual(rc, 2, err.getvalue())
            self.assertIn("newer than this kit", err.getvalue())
            self.assertIn("99.0.0", err.getvalue())
            self.assertEqual(mod.adopt(replace=True), 0)  # --replace overrides the refusal

    def test_an_unwritable_state_dir_is_a_warning_not_a_failure(self):
        """`init`/`migrate`/`validate` already ran and succeeded by the time `_write_adopt_digest` is called — an
        `OSError` writing the record must not turn that into a failed adopt (`setup.sh` would then print an
        untrue "adopt failed" line); it is a warning, and the normal exit code still applies."""
        (self.root / "state").write_text("not a directory\n", encoding="utf-8")  # state/ctx-adapter/... cannot be made
        r = self.adapter("adopt", KIT_CTX=str(self.fake), FAKE_CTX_OUT="ok: 0 docs checked\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warn: could not record the adopted digest under state/", r.stdout)
        self.assertIn("adopt --check will keep reading this store as behind", r.stdout)

    def test_findings_and_a_stale_record_both_print(self):
        """A store with a validation finding that is also behind must not have the `behind:` line swallowed just
        because findings (exit 3) wins the exit code — both lines reach stdout, the precedence only picks which
        exit code wins."""
        r = self.adapter("adopt", "--check", KIT_CTX=str(self.fake), FAKE_CTX_RC="3",
                          FAKE_CTX_ERR="SCHEMA_VIOLATION d/bad status\n")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)  # findings still win the exit code
        self.assertIn("finding: SCHEMA_VIOLATION d/bad status", r.stdout)
        self.assertIn("behind:", r.stdout)  # no record at all is also a stale record (see `_recorded_state`)


class AdoptNoValidate(Base):
    """`adopt --check --no-validate`: the same record comparison as a full `--check` (`behind`/`ahead`/`differs`),
    but without ever running `ctx validate` — so this path needs no ctx executable at all and can never take
    long or time out. "Is this an adopted store" is then a file test, the store's own `ctx-store.json` at the
    content root, rather than asking ctx — the fake `ctx` in these tests never writes that file for real, so a
    test that wants to look adopted writes a stand-in itself."""

    def digest_path(self) -> Path:
        return self.root / "state" / "ctx-adapter" / "adopted-kit.json"

    def adopt_once(self) -> None:
        """A full adopt against the fake ctx, so the store carries a current digest record — then a stand-in
        `ctx-store.json` (the fake never writes store files; the no-validate path tests this one file test on
        its own, never ctx's own `init`)."""
        r = self.adapter("adopt", KIT_CTX=str(self.fake), FAKE_CTX_OUT="ok: 0 docs checked\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (self.root / "ctx-store.json").write_text("{}\n", encoding="utf-8")

    def test_without_check_is_exit_2(self):
        r = self.adapter("adopt", "--no-validate")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--no-validate requires --check", r.stderr)

    def test_no_ctx_store_json_is_not_adopted(self):
        r = self.adapter("adopt", "--check", "--no-validate")  # no KIT_CTX either: must not need to resolve it
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("not adopted", r.stdout)

    def test_never_calls_ctx_even_when_one_is_available(self):
        """The cheap path does not just tolerate a missing ctx — it never calls it at all, proven against the
        fake, which logs every call it receives."""
        self.adopt_once()
        before = len(self.calls())
        r = self.adapter("adopt", "--check", "--no-validate", KIT_CTX=str(self.fake))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.calls()), before)

    def test_matching_record_is_clean(self):
        self.adopt_once()
        r = self.adapter("adopt", "--check", "--no-validate")  # no KIT_CTX: ctx is not even installed
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_stale_record_is_behind(self):
        self.adopt_once()
        self.digest_path().unlink()  # a store adopted before this digest existed carries no record at all
        r = self.adapter("adopt", "--check", "--no-validate")
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("behind:", r.stdout)

    def test_a_kept_file_on_record_is_differs(self):
        r = self.adapter("adopt", KIT_CTX=str(self.fake), FAKE_CTX_OUT="kept: .ctx/types/epic.json\nok: 1 adopted\n")
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        (self.root / "ctx-store.json").write_text("{}\n", encoding="utf-8")
        r = self.adapter("adopt", "--check", "--no-validate")
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("differs: .ctx/types/epic.json — kept (edited here); `ctx_adapter.py adopt --replace` "
                      "takes the kit's", r.stdout)

    def test_a_newer_recorded_version_is_ahead(self):
        self.adopt_once()
        state = json.loads(self.digest_path().read_text(encoding="utf-8"))
        state["digest"] = "0" * 64  # also stale, so this is a genuine mismatch, not just a version bump
        state["version"] = "99.0.0"  # newer than any real kit release
        self.digest_path().write_text(json.dumps(state), encoding="utf-8")
        r = self.adapter("adopt", "--check", "--no-validate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ahead: the store was adopted by kit 99.0.0", r.stdout)
        self.assertNotIn("behind", r.stdout)


class MigrateTargetToleratesKeptExit(unittest.TestCase):
    """`make migrate`'s second line must not fail the whole recipe when `ctx_adapter.py adopt` exits 5 (a store
    file was kept, not an error) — only a genuine failure (e.g. 3: validation findings) should. Tested as the
    POSIX `sh` idiom itself (a stub stands in for the adapter, its exit code controlled here), not a full `ctx` +
    env-store `make migrate` run — `adopt`'s own exit codes are already covered elsewhere (`AdoptBehind`, `Adopt`)."""

    def adopt_line(self) -> str:
        """The `migrate:` target's second recipe line, read from the Makefile itself so this test fails the
        moment the recipe's wording drifts from what it actually runs — never a copy of its own."""
        mk = (KIT / "context-db" / "Makefile").read_text(encoding="utf-8")
        m = re.search(r'\n\t(@CONTEXT_ROOT="\$\(CONTEXT\)" \$\(PY\) \$\(BIN\)/ctx_adapter\.py adopt;[^\n]*)\n', mk)
        self.assertIsNotNone(m, "migrate: target's `ctx_adapter.py adopt` recipe line not found or reworded")
        return m.group(1)

    def run_with_stub_rc(self, rc: int) -> int:
        # `make` hands the shell `$$` as a literal `$` (its own escaping) — do the same substitution here, then
        # stand in for `$(PY) $(BIN)/ctx_adapter.py adopt` with a stub that only exits `rc`: the idiom after the
        # `;` is what this test is about, not a real adopt run
        line = self.adopt_line().lstrip("@").replace("$$", "$")
        line = re.sub(r'CONTEXT_ROOT="\$\(CONTEXT\)" \$\(PY\) \$\(BIN\)/ctx_adapter\.py adopt', f"(exit {rc})", line)
        r = subprocess.run(["sh", "-c", line], capture_output=True, text=True)
        return r.returncode

    def test_ok_and_kept_both_succeed(self):
        self.assertEqual(self.run_with_stub_rc(0), 0)
        self.assertEqual(self.run_with_stub_rc(5), 0)

    def test_a_real_failure_still_fails_the_recipe(self):
        self.assertEqual(self.run_with_stub_rc(3), 3)  # validation findings
        self.assertEqual(self.run_with_stub_rc(2), 2)  # usage/I-O error
        self.assertEqual(self.run_with_stub_rc(6), 6)  # behind — `adopt --check` only, never plain `adopt`, but
                                                        # the idiom itself must still propagate any code but 0/5


class SemverParsing(unittest.TestCase):
    """`_parse_semver`: a tuple of ints, never a string comparison, and None for anything it cannot read as one."""

    def test_parses_with_or_without_a_leading_v(self):
        mod = load_adapter()
        self.assertEqual(mod._parse_semver("0.7.0"), (0, 7, 0))
        self.assertEqual(mod._parse_semver("v0.7.0"), (0, 7, 0))

    def test_unparsable_text_is_none(self):
        mod = load_adapter()
        for text in ("", "not-a-version", "0.7", "0.7.0-rc1", "0.7.0+build3"):
            self.assertIsNone(mod._parse_semver(text), text)

    def test_compares_numerically(self):
        mod = load_adapter()
        self.assertGreater(mod._parse_semver("0.10.0"), mod._parse_semver("0.9.0"))


class ResolveRule(Base):
    """The kit's `resolve.fields` names no field: a field match would outrank the context doc's own
    `resolve.section` match (`Tracker & links`), and every session doc carries the same key in its own
    `epic:` frontmatter field, so a field match would always win and `ctx resolve <key>` would always name
    the querying session, never the context doc the key belongs to. Proven against the pinned ctx, not a
    fake: a stub could not show which doc the store's own settings make `ctx resolve` prefer."""

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_resolve_returns_the_epic_doc_not_the_session_row(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        key = "acme/gadgets#3"
        (self.root / "gadgets").mkdir()
        (self.root / "gadgets" / "rollout.md").write_text(
            "---\ntitle: Gadgets\ntype: epic\ndomain: gadgets\nstatus: active\nupdated: 2026-01-05\n---\n"
            "# Gadgets\n\n## Tracker & links\n\n- Epic: " + key + "\n\n## Goal\n\ng\n\n"
            "## Key decisions & gotchas\n\n## Remaining work\n\n## Session log\n\n- 2026-01-05 — first\n",
            encoding="utf-8")
        self.sid = "sid-resolve-1"
        (self.root / "sessions" / "lane-topic.md").write_text(
            f"---\nsession: lane-topic\nsession_id: {self.sid}\nstatus: active\nepic: {key}\n"
            "updated: 2026-01-05\n---\n# Session: lane-topic\n\n## Notes\n\nfree text\n", encoding="utf-8")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)  # the new docs validate clean too
        r = self.adapter("ctx", "resolve", key, **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0].split(" · ", 1)[0].strip(), "gadgets/rollout")


SESSION_FOR_BRIEF = (
    "---\nsession: lane-topic\nsession_id: {sid}\nstatus: active\nepic: {epic}\nworking_on: step three\n"
    "responsibilities: " + ("owns the lane end to end " * 10) + "\nstats: turns 40 · ctx 100k\n"
    "heartbeat: 2026-01-05T10:00:00Z\nupdated: 2026-01-05\n---\n# Session: lane-topic\n\n## Notes\n\nfree text\n\n"
    "## Open PRs\n\n- acme/widgets#7 head aaa — waits on CI\n\n## Owns\n\n- the lane\n\n"
    "## Open decisions\n\n- none pending\n")
EPIC_FOR_BRIEF = (
    "---\ntitle: Widgets\ntype: epic\ndomain: widgets\nstatus: active\nupdated: 2026-01-05\n---\n# Widgets\n\n"
    "## Tracker & links\n\n- Epic: acme/widgets#42\n\n## Goal\n\ng\n\n## Key decisions & gotchas\n\n"
    "## Remaining work\n\n" + "\n".join(f"- step {n}" for n in range(1, 12)) +
    "\n\n## Session log\n\n- 2026-01-05 — first\n")


class CompactBrief(Base):
    """The `brief-session` hook's rewrite (#56, #420): one owner line naming this session and its own `epic:`
    frontmatter value verbatim — written before any context-doc lookup is attempted, so it never waits on
    `ctx resolve` — then the session's own brief re-budgeted (bookkeeping frontmatter dropped, the
    sections that matter led to the front), then — when a context doc resolved — its key and the head of its
    *Remaining work*. Proven against the pinned ctx: a fake could not show whether `ctx resolve`
    answers the way the adapter now assumes."""

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_owner_line_section_order_and_remaining_work_head(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        (self.root / "widgets").mkdir()
        (self.root / "widgets" / "rollout.md").write_text(EPIC_FOR_BRIEF, encoding="utf-8")
        self.sid = "sid-ct-1"
        (self.root / "sessions" / "lane-topic.md").write_text(
            SESSION_FOR_BRIEF.format(sid=self.sid, epic="acme/widgets#42"), encoding="utf-8")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)  # the new docs validate clean too
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), **env)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        out = r.stdout
        lines = out.splitlines()
        # the owner line is first, naming this session's own doc and its own `epic:` field verbatim — the
        # resolved context doc (below, from `ctx resolve`'s own section match: the store's `resolve.fields`
        # names no field, so a session's own `epic:` frontmatter never outranks it) only ever shows up in the
        # trailing block
        self.assertEqual(lines[0], "compacted — re-grounded from sessions/lane-topic and epic acme/widgets#42")
        # dropped bookkeeping
        for field in ("stats:", "heartbeat:", "session_id:", "ref:", "updated:", "sections:"):
            self.assertNotIn(field, out)
        # kept frontmatter, in the kept order
        self.assertIn("session: lane-topic", out)
        self.assertIn("epic: acme/widgets#42", out)
        self.assertIn("working_on: step three", out)
        self.assertIn("responsibilities: owns the lane", out)
        # body sections in the stated order (## Assumptions, ## Worktrees are absent here, so skipped)
        headings = re.findall(r"(?m)^## (.+)$", out)
        self.assertEqual(headings[:4], ["Open PRs", "Open decisions", "Owns", "Notes"])
        # the context doc's key and the head (not all 11) of its Remaining work
        self.assertIn("widgets/rollout:", out)
        self.assertIn("- step 1", out)
        self.assertIn("- step 8", out)
        self.assertNotIn("- step 9", out)
        # within the stated budgets
        mod = load_adapter()
        owner_bytes = len(lines[0].encode("utf-8")) + 1
        self.assertLessEqual(len(out.encode("utf-8")), owner_bytes + mod.BRIEF_BUDGET + mod.EPIC_BUDGET)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_no_epic_field_says_no_context_doc_and_appends_nothing(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        self.sid = "sid-ct-2"
        (self.root / "sessions" / "lane-topic.md").write_text(
            f"---\nsession: lane-topic\nsession_id: {self.sid}\nstatus: active\nworking_on: x\n---\n"
            "# Session: lane-topic\n\n## Notes\n\nhi\n", encoding="utf-8")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), **env)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout.splitlines()[0], "compacted — re-grounded from sessions/lane-topic and no context doc")
        self.assertNotIn(":\n", r.stdout.split("\n\n")[-1])  # no trailing context-doc block appended

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_an_epic_field_that_resolves_to_nothing_still_names_the_epic_key(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        self.sid = "sid-ct-3"
        (self.root / "sessions" / "lane-topic.md").write_text(
            SESSION_FOR_BRIEF.format(sid=self.sid, epic="acme/widgets#99"), encoding="utf-8")  # no such doc
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), **env)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        # the owner line names the session row's own `epic:` value regardless of whether it resolves to a doc
        self.assertEqual(r.stdout.splitlines()[0], "compacted — re-grounded from sessions/lane-topic and epic acme/widgets#99")
        self.assertNotIn(":\n", r.stdout.split("\n\n")[-1])  # no trailing context-doc block appended
        self.assertNotIn("skipped (hook deadline)", r.stdout)  # a plain no-match, not a timeout

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_no_session_id_or_no_row_stays_silent(self):
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        anonymous = json.dumps({"cwd": str(self.t), "source": "compact"})
        r = self.adapter("hook", "brief-session", stdin=anonymous, **env)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.sid = "sid-ct-missing"  # registered nowhere
        r = self.adapter("hook", "brief-session", stdin=self.payload(source="compact"), **env)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))


BRIEF_FOR_DEADLINE = ("sessions/lane-topic (session, 10 bytes)\nsession: lane-topic\nepic: acme/widgets#42\n\n"
                      "# Session: lane-topic\n\n## Notes\n\nhi\n")


class CompactBriefDeadline(Base):
    """A compact brief used to make up to three ctx calls in a row (`brief --session`, `resolve`,
    `get --section`, each also able to wait `CTX_LOCK_TIMEOUT` on a lock) against the hooks' own 10s
    `timeout` (hooks/hooks.json, settings.json), building its whole answer before printing a byte of it — a
    harness kill lost even the owner line and session brief it had already computed. `COMPACT_DEADLINE` now
    bounds the whole call; the owner line (naming this session's own `epic:` frontmatter value verbatim, never
    a resolved doc) and the session brief are written and flushed before any context-doc lookup is even
    attempted (#420) — not merely before the slowest of them — and each lookup's timeout is recomputed from
    `remaining()` right before that call, never one value computed once and handed to two calls in a row
    (the review on this change). A slow or timed-out context-doc lookup is replaced with one line rather than
    risking the rest. A stubbed `_ctx` (not a real sleep, no real `ctx` needed) stands in for a slow
    `resolve`."""

    def setUp(self):
        super().setUp()
        self.mod = load_adapter()
        self.mod.resolve = lambda: (self.fake, "override")
        self.mod._context_root = lambda: self.root

    def run_hook(self, stdin: str) -> str:
        import contextlib
        import io
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CTX_STORE": ""}), \
             mock.patch.object(sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
            self.mod.hook("brief-session")
        return out.getvalue()

    def test_a_slow_resolve_is_skipped_but_the_owner_line_and_brief_still_print(self):
        def slow_ctx(ctx, store, *args, timeout=self.mod.HOOK_TIMEOUT):
            if args[0] == "brief":
                return subprocess.CompletedProcess(args, 0, stdout=BRIEF_FOR_DEADLINE, stderr="")
            raise self.mod.subprocess.TimeoutExpired("ctx", timeout)  # resolve/find/get: always too slow
        self.mod._ctx = slow_ctx
        out = self.run_hook(self.payload(source="compact"))
        # the owner line names the session row's own `epic:` value, not a resolved doc — it never waits on resolve
        self.assertTrue(out.startswith("compacted — re-grounded from sessions/lane-topic and epic acme/widgets#42\n"), out)
        self.assertIn("session: lane-topic", out)
        self.assertIn("context doc: skipped (hook deadline)", out)

    def test_a_fast_resolve_prints_the_full_output_as_before(self):
        def fast_ctx(ctx, store, *args, timeout=self.mod.HOOK_TIMEOUT):
            if args[0] == "brief":
                return subprocess.CompletedProcess(args, 0, stdout=BRIEF_FOR_DEADLINE, stderr="")
            if args[0] == "resolve":
                return subprocess.CompletedProcess(args, 0, stdout="widgets/rollout · epic\n", stderr="")
            if args[0] == "get":
                return subprocess.CompletedProcess(args, 0, stdout="- step 1\n- step 2\n", stderr="")
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        self.mod._ctx = fast_ctx
        out = self.run_hook(self.payload(source="compact"))
        self.assertTrue(out.startswith("compacted — re-grounded from sessions/lane-topic and epic acme/widgets#42\n"), out)
        self.assertNotIn("skipped (hook deadline)", out)
        self.assertIn("widgets/rollout:", out)
        self.assertIn("- step 1", out)
        self.assertIn("- step 2", out)

    def test_deadline_leaves_margin_under_the_hooks_own_timeout(self):
        for path in (KIT / "hooks" / "hooks.json", KIT / "settings.json"):
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            timeouts = [h["timeout"] for groups in hooks.values() for g in groups for h in g["hooks"]
                        if "brief-session" in h["command"]]
            self.assertTrue(timeouts, path)
            for t in timeouts:
                self.assertLessEqual(self.mod.COMPACT_DEADLINE + 1, t, path)

    def test_owner_line_and_brief_are_written_before_a_near_timeout_resolve_and_a_timed_out_get(self):
        """The exact shape the review flagged: a `resolve` that answers just under `EPIC_LOOKUP_TIMEOUT` (naming
        the context doc, not this session), then a `get` (the *Remaining work* fetch) that times out. Each
        call's timeout is recomputed from the fake clock right before it is made — `get` does not inherit the
        stale, already-almost-spent timeout `resolve` was given — and the owner line plus brief are on stdout
        (write #1) well before either lookup is attempted (writes #2+, once skipped)."""
        import contextlib
        import io
        clock = [0.0]
        writes: list[tuple[float, str]] = []

        class RecordingStdout(io.StringIO):
            def write(self, s):
                if s:
                    writes.append((clock[0], s))
                return super().write(s)

        def fake_monotonic():
            return clock[0]

        def ctx_with_a_near_timeout_resolve_then_a_timed_out_get(ctx, store, *args, timeout=self.mod.HOOK_TIMEOUT):
            if args[0] == "brief":
                return subprocess.CompletedProcess(args, 0, stdout=BRIEF_FOR_DEADLINE, stderr="")
            if args[0] == "resolve":
                clock[0] += 2.9  # answers just under EPIC_LOOKUP_TIMEOUT, naming the context doc
                return subprocess.CompletedProcess(args, 0, stdout="widgets/rollout · epic\n", stderr="")
            if args[0] == "get":
                clock[0] += timeout  # the per-call timeout it was actually given, recomputed, not reused from resolve
                raise self.mod.subprocess.TimeoutExpired("ctx", timeout)
            raise AssertionError(f"unexpected ctx verb for this scenario: {args[0]}")

        self.mod._ctx = ctx_with_a_near_timeout_resolve_then_a_timed_out_get
        with mock.patch.object(self.mod.time, "monotonic", fake_monotonic), \
             mock.patch.dict(os.environ, {"CTX_STORE": ""}), \
             mock.patch.object(sys, "stdin", io.StringIO(self.payload(source="compact"))), \
             contextlib.redirect_stdout(RecordingStdout()):
            self.mod.hook("brief-session")

        self.assertTrue(writes, "nothing was written")
        owner_and_brief_clock = writes[0][0]
        later_clocks = [c for c, _ in writes[1:]]
        self.assertEqual(owner_and_brief_clock, 0.0)  # written before either lookup ran
        self.assertTrue(all(c > owner_and_brief_clock for c in later_clocks), writes)
        full = "".join(s for _, s in writes)
        self.assertTrue(full.startswith("compacted — re-grounded from sessions/lane-topic and epic acme/widgets#42\n"))
        self.assertIn("context doc: skipped (hook deadline)", full)
        # get got its own timeout (remaining after resolve's 2.9s), not resolve's: it did not also wait ~3s
        # of a reused value plus whatever resolve already spent — the two lookups together stayed well under
        # the deadline, not anywhere near the ~11s a reused timeout could add up to
        self.assertLessEqual(clock[0], self.mod.COMPACT_DEADLINE + 1)


    def test_a_session_row_from_a_store_not_re_adopted_yet_is_no_context_doc(self):
        """A store adopted before the kit dropped `resolve.fields` still answers `ctx resolve` with the
        session's own row until `adopt` runs again. `_resolve_epic_doc` reads that as no context doc, never
        as the context doc, and makes no second call."""
        calls = []

        def ctx_naming_the_session(ctx, store, *args, timeout=self.mod.HOOK_TIMEOUT):
            calls.append(args[0])
            return subprocess.CompletedProcess(args, 0, stdout="sessions/lane-topic · session\n", stderr="")

        self.mod._ctx = ctx_naming_the_session
        self.assertIsNone(self.mod._resolve_epic_doc(self.fake, [], "acme/widgets#42", lambda: 9.0))
        self.assertEqual(calls, ["resolve"])


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

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_owner_rule_follows_the_named_actor_not_the_server(self):
        """The kit's `mcp.actors` pattern (session.py's own NAME_RE, #393) plus the session type's `owner` rule:
        a write naming the doc's own actor succeeds, another actor is NOT_OWNER, and one outside the pattern
        (a leading '-', which CTX_ACTOR never allows) is USAGE before it ever reaches the owner check."""
        env = dict(KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1")
        self.assertEqual(self.adapter("adopt", **env).returncode, 0)
        (self.root / "sessions").mkdir(exist_ok=True)
        (self.root / "sessions" / "foo-bar.md").write_text(
            "---\nsession: foo-bar\nstatus: active\n---\n\n## Owns\n\n- x\n", encoding="utf-8")

        def fm(ident, value, actor=None):
            args = {"doc": "sessions/foo-bar", "field": "working_on", "value": value}
            if actor is not None:
                args["actor"] = actor
            return {"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {"name": "ctx_fm", "arguments": args}}

        own, foreign, outside = self.mcp(fm(1, "w1", "foo-bar"), fm(2, "w2", "baz-qux"), fm(3, "w3", "-nope"), **env)
        self.assertFalse(own["result"].get("isError"), own)
        self.assertTrue(foreign["result"]["isError"])
        self.assertEqual(foreign["result"]["content"][0]["text"], "NOT_OWNER sessions/foo-bar: doc is owned by another actor")
        self.assertTrue(outside["result"]["isError"])
        self.assertEqual(outside["result"]["content"][0]["text"], "USAGE actor: bad command line")
        self.assertIn("working_on: w1\n", (self.root / "sessions" / "foo-bar.md").read_text(encoding="utf-8"))


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
