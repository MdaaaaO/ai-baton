"""verified.py — pr-open's `## Verified` section (issue #100): `run` fills the block from a real command,
`check` refuses a bare claim and a stale recorded head, reusing evidence_check.py's evidence-shape checks.
Stdlib unittest; the env-key tests point kb/kit_profile at a mktemp store, never the live one.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
SKILL = KIT / "skills" / "pr-open"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(SKILL))

import kb  # noqa: E402
import kit_profile  # noqa: E402
import verified as v  # noqa: E402


# ── run: bound_tail / strip_ansi ────────────────────────────────────────────────────────────
class BoundTail(unittest.TestCase):
    def test_strips_ansi_colour_codes(self):
        self.assertEqual(v.strip_ansi("\x1b[32mgreen\x1b[0m\n"), "green\n")

    def test_keeps_short_output_whole(self):
        self.assertEqual(v.bound_tail("a\nb\nc\n"), "a\nb\nc")

    def test_bounds_to_the_last_15_lines(self):
        text = "\n".join(f"line{i}" for i in range(1, 21))  # 20 lines
        out = v.bound_tail(text)
        self.assertEqual(out.splitlines(), [f"line{i}" for i in range(6, 21)])  # last 15

    def test_bounds_to_1200_bytes_even_under_15_lines(self):
        text = "\n".join("x" * 200 for _ in range(10))  # 10 lines, 2000+ bytes
        out = v.bound_tail(text)
        self.assertLessEqual(len(out.encode("utf-8")), v.MAX_BYTES)

    def test_ansi_stripped_before_the_line_and_byte_bound(self):
        text = "\x1b[31m" + ("x" * 300 + "\n") * 10
        out = v.bound_tail(text)
        self.assertNotIn("\x1b", out)
        self.assertLessEqual(len(out.encode("utf-8")), v.MAX_BYTES)


# ── run: run_cmd ─────────────────────────────────────────────────────────────────────────────
class RunCmd(unittest.TestCase):
    def test_success_tail_and_exit_0(self):
        r = v.run_cmd(str(KIT), "printf 'alpha\\nbeta\\n'", timeout=5)
        self.assertEqual(r["exit"], 0)
        self.assertEqual(r["tail"], "alpha\nbeta")
        self.assertTrue(r["head"])  # a real sha from this checkout

    def test_failure_exit_is_reported_not_hidden(self):
        r = v.run_cmd(str(KIT), "echo boom; exit 7", timeout=5)
        self.assertEqual(r["exit"], 7)
        self.assertEqual(r["tail"], "boom")

    def test_timeout_reports_the_sentinel_not_a_number(self):
        r = v.run_cmd(str(KIT), "sleep 5", timeout=1)
        self.assertEqual(r["exit"], "timeout")

    def test_render_block_shape(self):
        r = {"command": "make test", "head": "abc1234de", "exit": 0, "tail": "ok"}
        block = v.render_block(r, 300)
        self.assertIn("Command: `make test` (timeout 300s)", block)
        self.assertIn("Head: `abc1234de`", block)
        self.assertIn("Exit: `0`", block)
        self.assertIn("```\nok\n```", block)


class RunCli(unittest.TestCase):
    def _run(self, *args):
        r = subprocess.run([sys.executable, str(SKILL / "verified.py"), "run", *args],
                            capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def test_cli_prints_the_block(self):
        rc, out, err = self._run("--repo-dir", str(KIT), "--cmd", "echo hi", "--timeout", "5")
        self.assertEqual(rc, 0)
        self.assertIn("Exit: `0`", out)
        self.assertIn("hi", out)
        self.assertEqual(err, "")


# ── check: bare claim ────────────────────────────────────────────────────────────────────────
class CheckBareClaim(unittest.TestCase):
    def test_no_verified_section_passes(self):
        body = "## What\nstuff\n"
        self.assertIsNone(v.verified_section(body))

    def test_eval_near_miss_tests_pass_with_no_output_is_refused(self):
        body = "## Verified\ntests pass\n\n## Machines\nnothing\n"
        start, section = v.verified_section(body)
        bad = v.bare_claims(start, section)
        self.assertEqual([c for _, c in bad], ["tests pass"])

    def test_cited_commands_list_passes(self):
        body = "## Verified\n- `pytest -q` → `12 passed`\n- `ruff check .` → `All checks passed!`\n"
        start, section = v.verified_section(body)
        self.assertEqual(v.bare_claims(start, section), [])

    def test_not_verified_locally_passes(self):
        body = "## Verified\nNot verified locally: no network access here\n"
        start, section = v.verified_section(body)
        self.assertEqual(v.bare_claims(start, section), [])

    def test_structured_run_block_with_output_passes(self):
        body = ("## Verified\nCommand: `make test` (timeout 300s)\nHead: `abc1234de`\nExit: `0`\n\n"
                 "```\nall good\n```\n")
        start, section = v.verified_section(body)
        self.assertEqual(v.bare_claims(start, section), [])

    def test_structured_run_block_with_empty_tail_is_bare(self):
        body = "## Verified\nCommand: `make test`\nExit: `0`\n\n```\n```\n"
        start, section = v.verified_section(body)
        bad = v.bare_claims(start, section)
        self.assertTrue(bad)
        self.assertIn("no pasted output", bad[0][1])

    def test_mixed_list_names_only_the_bare_line(self):
        body = "## Verified\n- `pytest -q` → `12 passed`\n- it works trust me\n"
        start, section = v.verified_section(body)
        bad = v.bare_claims(start, section)
        self.assertEqual([c for _, c in bad], ["it works trust me"])


class CheckCli(unittest.TestCase):
    def _run(self, path, *args):
        r = subprocess.run([sys.executable, str(SKILL / "verified.py"), "check", path, *args],
                            capture_output=True, text=True)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def _write(self, text: str) -> str:
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_exit_3_on_a_bare_claim(self):
        path = self._write("## Verified\ntests pass\n")
        rc, out, err = self._run(path)
        self.assertEqual(rc, 3)
        self.assertIn("tests pass", err)

    def test_exit_0_on_cited_commands(self):
        path = self._write("## Verified\n- `pytest -q` → `ok`\n")
        rc, out, err = self._run(path)
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")

    def test_stale_head_via_a_stubbed_gh_on_path(self):
        path = self._write("## Verified\nCommand: `make test`\nHead: `abc1234de`\nExit: `0`\n\n```\nok\n```\n")
        fake_bin = tempfile.mkdtemp()
        gh_path = Path(fake_bin) / "gh"
        gh_path.write_text("#!/bin/sh\necho deadbeef99999999999999999999999999999999\n")
        gh_path.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
        r = subprocess.run([sys.executable, str(SKILL / "verified.py"), "check", path,
                            "--pr", "acme/widgets", "12"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 3)
        self.assertIn("STALE abc1234de → deadbeef9", r.stdout)

    def test_matching_head_is_not_stale(self):
        path = self._write("## Verified\nCommand: `make test`\nHead: `abc1234de`\nExit: `0`\n\n```\nok\n```\n")
        fake_bin = tempfile.mkdtemp()
        gh_path = Path(fake_bin) / "gh"
        gh_path.write_text("#!/bin/sh\necho abc1234de00000000000000000000000000\n")
        gh_path.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
        r = subprocess.run([sys.executable, str(SKILL / "verified.py"), "check", path,
                            "--pr", "acme/widgets", "12"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("STALE", r.stdout)


# ── env key: verify.repos.<owner/repo>.cmd / .timeout ───────────────────────────────────────
class EnvKey(unittest.TestCase):
    """Points kb/kit_profile at a throw-away store (never .context/, per the worker brief's hard rules)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR)
        kb.ENV = self.env
        kb.CTX = Path(self.tmp.name)
        kb.ROOT = Path(self.tmp.name).parent
        kit_profile.ENV_DIR = self.env
        kb.init_blank()
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()

    def tearDown(self):
        kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()

    def cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = kb.main(["kb.py", *args]) or 0
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
        return rc, out.getvalue(), err.getvalue()

    def test_absent_is_a_valid_empty_map(self):
        self.assertEqual(kit_profile.get("verify.repos", {}), {})

    def test_present_round_trips_cmd_and_timeout(self):
        slug = "acme/widgets"
        rc, _out, err = self.cli("config-set", "verify", f'{{"repos": {{"{slug}": {{"cmd": "make test", "timeout": 120}}}}}}')
        self.assertEqual(rc, 0, err)
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        repos = kit_profile.get("verify.repos", {})
        self.assertEqual(repos[slug]["cmd"], "make test")
        self.assertEqual(repos[slug]["timeout"], 120)

    def test_blank_store_satisfies_kit_verify_config_key_drift(self):
        self.assertEqual(kb.config_key_drift(), [])


if __name__ == "__main__":
    unittest.main()
