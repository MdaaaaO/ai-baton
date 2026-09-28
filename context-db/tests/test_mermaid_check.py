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


if __name__ == "__main__":
    unittest.main()
