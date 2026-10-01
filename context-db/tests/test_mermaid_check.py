"""pr-open's mermaid-check.mjs (#166): a CRLF-terminated ```mermaid fence must still be found (a PR body typed
on Windows or pasted from a browser), and a file with zero mermaid blocks must fail the check unless
`--allow-none` is given (a plan called for diagrams that never got drawn should not report success silently).
Runs the real script under `node` against stub `jsdom` / `dompurify` / `mermaid` packages (no npm install
needed — the packages just satisfy the script's imports; `mermaid.parse` fails only on a marker string this
test controls). Skipped when `node` is not on PATH. Run: make -C .claude/context-db test."""
from __future__ import annotations
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SCRIPT = KIT / "skills" / "pr-open" / "mermaid-check.mjs"

NODE = shutil.which("node")

_STUBS = {
    "jsdom": (
        '{"name":"jsdom","version":"0.0.0","main":"index.js"}',
        "class JSDOM {\n  constructor(html, opts) { this.window = { document: {} }; }\n}\n"
        "module.exports = { JSDOM };\n",
    ),
    "dompurify": (
        '{"name":"dompurify","version":"0.0.0","main":"index.js"}',
        "module.exports = function (window) { return {}; };\n",
    ),
    # `parse` throws only for a block containing the sentinel this test writes, so a real syntax bug is
    # never needed to prove the CRLF fence is found and reaches the parser.
    "mermaid": (
        '{"name":"mermaid","version":"0.0.0","main":"index.js"}',
        "module.exports = {\n  initialize() {},\n  async parse(text) {\n"
        "    if (text.includes('SENTINEL_BAD')) { throw new Error('bad syntax here'); }\n"
        "    return { diagramType: 'flowchart' };\n  }\n};\n",
    ),
}


def _make_scratch() -> Path:
    tmp = Path(tempfile.mkdtemp())
    for name, (pkg, index) in _STUBS.items():
        d = tmp / "node_modules" / name
        d.mkdir(parents=True)
        (d / "package.json").write_text(pkg, encoding="utf-8")
        (d / "index.js").write_text(index, encoding="utf-8")
    return tmp


@unittest.skipUnless(NODE, "node not on PATH")
class MermaidCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = _make_scratch()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def run_check(self, filename: str, content: str, *extra_args: str):
        f = self.scratch / filename
        f.write_bytes(content.encode("utf-8"))
        r = subprocess.run([NODE, str(SCRIPT), *extra_args, str(f)], cwd=self.scratch,
                            capture_output=True, text=True)
        return r

    def test_crlf_fence_is_found_and_parsed(self):
        # a CRLF body (Windows-authored or pasted from a browser) used to make the fence regex miss the
        # block entirely, so a broken diagram was reported as "no mermaid blocks" and exit 0 (success)
        body = "Overview\r\n\r\n```mermaid\r\nSENTINEL_BAD\r\n```\r\n"
        r = self.run_check("crlf.md", body)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL", r.stdout)
        self.assertNotIn("no mermaid blocks", r.stdout)

    def test_crlf_fence_with_valid_content_still_parses_ok(self):
        body = "Overview\r\n\r\n```mermaid\r\nflowchart LR\r\n  a --> b\r\n```\r\n"
        r = self.run_check("crlf-ok.md", body)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK (flowchart)", r.stdout)

    def test_zero_blocks_fails_unless_allow_none(self):
        r = self.run_check("empty.md", "no diagrams here\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        r2 = self.run_check("empty2.md", "no diagrams here\n", "--allow-none")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)

    def test_missing_deps_print_install_hint_and_exit_2(self):
        # run from a bare scratch dir with none of the stub node_modules — the trap the second bug report
        # described: the check silently fails (an uncaught exception, exit 1, no guidance) when run from a
        # dir that never had `npm ci` run in it, or from the wrong CWD after it was.
        bare = Path(tempfile.mkdtemp())
        try:
            f = bare / "body.md"
            f.write_text("no diagrams here\n", encoding="utf-8")
            r = subprocess.run([NODE, str(SCRIPT), str(f)], cwd=bare, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("npm ci", r.stderr)
            self.assertIn("package.json", r.stderr)
        finally:
            shutil.rmtree(bare, ignore_errors=True)


class MermaidCheckRealDepsTest(unittest.TestCase):
    """The real jsdom/dompurify/mermaid, not the stubs above: MermaidCheckTest's stub `mermaid` never
    touches DOMPurify, so it could not catch the bug where mermaid's own DOMPurify instance came up without a
    window and `DOMPurify.addHook is not a function` failed every flowchart with a labelled node. Installs the
    pinned deps with `npm ci --offline` into a scratch dir (no network call either way); skips cleanly, with a
    stated reason, when node/npm are missing or the gate's npm cache lacks them."""

    scratch: Path | None = None

    @classmethod
    def setUpClass(cls):
        npm = shutil.which("npm")
        if not NODE or not npm:
            raise unittest.SkipTest("node and npm needed")
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(SCRIPT.parent / "package.json", tmp / "package.json")
        shutil.copy(SCRIPT.parent / "package-lock.json", tmp / "package-lock.json")
        r = subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--offline"], cwd=tmp,
                            capture_output=True, text=True)
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise unittest.SkipTest("jsdom/dompurify/mermaid not installable offline on this machine")
        cls.scratch = tmp

    @classmethod
    def tearDownClass(cls):
        if cls.scratch:
            shutil.rmtree(cls.scratch, ignore_errors=True)

    def run_check(self, filename: str, content: str):
        f = self.scratch / filename
        f.write_text(content, encoding="utf-8")
        return subprocess.run([NODE, str(SCRIPT), str(f)], cwd=self.scratch, capture_output=True, text=True)

    def test_labelled_flowchart_node_parses_ok(self):
        body = '```mermaid\nflowchart LR\n  a["label (x)"] --> b\n```\n'
        r = self.run_check("labelled.md", body)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK (flowchart", r.stdout)

    def test_real_syntax_error_still_fails(self):
        body = '```mermaid\nflowchart LR\n  a --> --> --> not a diagram\n```\n'
        r = self.run_check("broken.md", body)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL", r.stdout)


if __name__ == "__main__":
    unittest.main()
