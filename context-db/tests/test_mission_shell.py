"""mission-control shell: the page template and its parts, built by mission.py with no stand-in, and check.py, the gate
on a built page.

The page is built from the placeholder store tests.test_mission plants (imported, not copied) and from a blank one, and
check.py reads it as a subprocess the way the skill runs it, so the exit codes and lines are the real ones. The browser
side cannot run here: the JS parts are held to the page's contract by reading their text (nothing remote, nothing
written as markup, no browser dialog, the stale rule and the stamp shape the generator uses). Paths a command prints are
compared resolved on both sides: a temp dir may sit behind a symlink. Nothing here touches a real store; every value is
a placeholder. Stdlib unittest.
Run: make -C $BATON/context-db test T=test_mission_shell."""
from __future__ import annotations
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ENGINE = Path(__file__).resolve().parents[1]
KIT = ENGINE.parent
BIN = ENGINE / "bin"
SKILL = KIT / "skills" / "mission-control"
CHECK = SKILL / "check.py"
TEMPLATE = SKILL / "page.tpl.html"
PARTS = SKILL / "parts"
sys.path.insert(0, str(BIN))
from tests.test_mission import (  # noqa: E402 — the fixture store and the run helpers of the build suite
    EMAIL, build_base, engine_env, plant, run_mission, run_tool, script_json)
import gen_sessions  # noqa: E402 — same dir: the stale rule the page mirrors
import mission  # noqa: E402 — same dir: the stamp format, the part syntax

SECTIONS = ("sessions", "cost", "catalog", "priorities", "queues", "pointers")
TEMPLATE_LABEL = "skills/mission-control/page.tpl.html"  # what build.json names the kit's own template
FORGE_HOSTS = {"github.com", "tracker.example"}  # the only hosts the placeholder store's links point at
BUDGET = 45 * 1024  # the template and its parts together

_TMP = None
BASE: Path  # the placeholder store
BLANK: Path  # a store holding only what `kb.py init --blank` writes
BUILT: Path  # the kit's own template built from BASE (no --template: the default resolution is under test)
BUILT_BLANK: Path  # ... from BLANK


def built(root: Path, out: Path) -> Path:
    r = run_mission(root, "build", "--out", out)
    if r.returncode:
        raise RuntimeError(f"fixture: the build failed: {r.stdout}{r.stderr}")
    return out


def setUpModule():
    global _TMP, BASE, BLANK, BUILT, BUILT_BLANK
    _TMP = tempfile.TemporaryDirectory(prefix="kit-shell-")
    unittest.addModuleCleanup(_TMP.cleanup)
    tmp = Path(_TMP.name)
    BASE = build_base(tmp)
    BLANK = tmp / "blank" / ".context"
    BLANK.mkdir(parents=True)
    r = run_tool(BIN / "kb.py", BLANK, "init", "--blank")
    if r.returncode:
        raise RuntimeError(f"fixture: kb.py init --blank failed: {r.stderr.strip()}")
    BUILT = built(BASE, tmp / "built")
    BUILT_BLANK = built(BLANK, tmp / "built-blank")


def text(directory: Path, name: str) -> str:
    return (directory / name).read_text(encoding="utf-8")


_CHECK = None


def check_module():
    """check.py as a module, for the table of planted shapes; the exit codes go through the subprocess below. Loaded on
    first use, so a kit without the file fails the tests that need it and not the import of this module."""
    global _CHECK
    if _CHECK is None:
        spec = importlib.util.spec_from_file_location("mission_check", CHECK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CHECK = module
    return _CHECK


def one_line(stream: str) -> bool:
    return len(stream.splitlines()) == 1 and "Traceback" not in stream


# ── the template and its parts, as they sit in the kit ───────────────────────────────────────────────────────
class Files(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.parts = {p.name: p.read_text(encoding="utf-8") for p in sorted(PARTS.iterdir())}
        cls.scripts = {n: t for n, t in cls.parts.items() if n.endswith(".js")}
        cls.everything = {"page.tpl.html": cls.template, **cls.parts}

    def test_every_part_the_template_names_exists_and_none_is_left_over(self):
        named = mission.PART_RE.findall(self.template)
        self.assertTrue(named)
        self.assertEqual(len(named), len(set(named)), "a part is inlined once")
        self.assertEqual(sorted(named), sorted(self.parts))
        for name in named:
            self.assertTrue(mission.PART_NAME_RE.match(name), name)
            self.assertTrue((PARTS / name).is_file(), name)

    def test_the_template_fills_only_tokens_the_generator_knows_and_the_parts_hold_none(self):
        names = re.findall(r"\{\{([^{}]*)\}\}", self.template)
        for name in names:
            self.assertTrue(name in ("KIT_VERSION", "DATA_JSON", "BUILD_JSON") or name.startswith("part:"), name)
        for name in ("DATA_JSON", "BUILD_JSON"):
            self.assertEqual(names.count(name), 1, name)
        for name, body in self.parts.items():
            self.assertNotIn("{{", body, name)

    def test_the_template_and_its_parts_stay_under_the_size_budget(self):
        total = len(TEMPLATE.read_bytes()) + sum(len(p.read_bytes()) for p in PARTS.iterdir())
        self.assertLess(total, BUDGET)

    def test_six_sections_six_tabs_and_the_label(self):
        for sid in SECTIONS:
            self.assertEqual(self.template.count(f'<section id="{sid}">'), 1, sid)
            self.assertEqual(self.template.count(f'<a id="tab-{sid}" href="#{sid}">'), 1, sid)
        self.assertIn("data as of", self.template)
        self.assertIn("<noscript>", self.template)
        tabs = re.search(r"const TABS = \[([^\]]*)\]", self.parts["app.js"])
        self.assertEqual(re.findall(r"'(\w+)'", tabs.group(1)), list(SECTIONS))
        table = re.search(r"const RENDER = \{(.*?)\};", self.parts["sections.js"], re.S)
        self.assertEqual(re.findall(r"(\w+): render", table.group(1)), list(SECTIONS))

    def test_one_script_runs_and_the_other_two_are_json_blocks(self):
        self.assertEqual(self.template.count("<script"), 3)
        self.assertEqual(re.findall(r'<script id="(\w+)" type="application/json">', self.template), ["data", "build"])
        self.assertEqual(self.template.count("'use strict';"), 1)

    def test_nothing_in_the_template_or_the_parts_reaches_the_network(self):
        markup = ("http://", "https://", "<script src", "<link", "<img", "<iframe", "<object", "<embed", "<base",
                  "<form", "@import", "url(")
        scripts = ("fetch", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "new Image", "new Audio",
                   "importScripts")
        for name, body in self.everything.items():
            for needle in markup:
                self.assertNotIn(needle, body, f"{needle} in {name}")
            if name.endswith(".js"):
                for needle in scripts:
                    self.assertNotIn(needle, body, f"{needle} in {name}")

    def test_the_scripts_write_no_markup_and_open_no_dialog(self):
        for name, body in self.scripts.items():
            for needle in ("innerHTML", "outerHTML", "insertAdjacentHTML", "createContextualFragment", "document.write",
                           "eval(", "new Function", "alert(", "confirm(", "prompt(", "window.open"):
                self.assertNotIn(needle, body, f"{needle} in {name}")

    def test_no_script_part_trips_a_pattern_the_gate_looks_for(self):
        """The gate's own table, applied to each part's source: a hit is named by part here, by page in RealTemplate."""
        for name, body in self.scripts.items():
            self.assertEqual(check_module().calls(body), [], name)

    def test_no_part_can_end_its_block_early(self):
        for name, body in self.parts.items():
            self.assertNotIn("</", body, name)
        for name, body in self.scripts.items():
            self.assertNotIn("<!--", body, name)

    def test_the_scripts_store_one_key_and_nothing_else(self):
        joined = "\n".join(self.scripts.values())
        self.assertIn("const TAB_KEY = 'mission.tab';", joined)
        calls = re.findall(r"\blocalStorage\.(\w+)\(([^)]*)\)", joined)
        self.assertTrue(calls)
        for method, argument in calls:
            self.assertIn(method, ("getItem", "setItem"))
            self.assertTrue(argument.startswith("TAB_KEY"), argument)
        for needle in ("sessionStorage", "document.cookie", "indexedDB"):
            self.assertNotIn(needle, joined)

    def test_the_scripts_stay_inside_es2017(self):
        """A text heuristic (no JS engine runs here): the syntax and library calls that arrived after ES2017."""
        later = [
            (r"\?\.(?!\d)", "optional chaining"), (r"\?\?", "nullish coalescing"), (r"catch\s*\{", "optional catch binding"),
            (r"for\s+await\b", "for await"), (r"async\s*\*", "async generator"), (r"\{\s*\.\.\.", "object spread"),
            (r",\s*\.\.\.[\w.]+\s*\}", "object spread"), (r"^\s*(?:import|export)\b", "module syntax"),
            (r"\bimport\s*\(", "dynamic import"), (r"\(\?<[=!A-Za-z]", "lookbehind or named group"),
            (r"\\p\{", "unicode property escape"), (r"\d_\d", "numeric separator"),
            (r"\.(?:flat|flatMap|trimStart|trimEnd|matchAll|replaceAll|at)\(", "a post-ES2017 method"),
            (r"\b(?:Object\.fromEntries|Object\.hasOwn|globalThis|BigInt|structuredClone)\b", "a post-ES2017 global"),
        ]
        for name, body in self.scripts.items():
            for pattern, what in later:
                self.assertIsNone(re.search(pattern, body, re.M), f"{what} in {name}")

    def test_the_stale_rule_and_the_stamp_shape_mirror_the_generator(self):
        util = self.parts["util.js"]
        self.assertEqual(mission.STAMP_FMT, "%Y-%m-%dT%H:%M:%SZ")
        self.assertIn(r"const STAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;", util)
        rule = re.search(r"now - ms > \(Number\(staleHours\) \|\| (\d+)\) \* 3600e3", util)
        self.assertIsNotNone(rule, "older than stale_hours, strictly")
        self.assertEqual(int(rule.group(1)), gen_sessions.STALE_HOURS)
        shown = re.findall(r"data\.stale_hours \|\| (\d+)", self.parts["sections.js"])
        self.assertEqual(shown, [str(gen_sessions.STALE_HOURS)])


# ── the kit's own template, built ────────────────────────────────────────────────────────────────────────────
class RealTemplate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = text(BUILT, "index.html")

    def test_the_default_resolution_finds_the_kits_own_template(self):
        build = json.loads(text(BUILT, "build.json"))
        self.assertEqual(build["template"], TEMPLATE_LABEL)
        self.assertEqual(build["kit_version"], (KIT / "VERSION").read_text(encoding="utf-8").strip())
        self.assertIn(f"(kit {build['kit_version']})", self.page)

    def test_every_token_is_filled_and_the_blocks_round_trip(self):
        self.assertNotIn("{{", self.page)
        self.assertEqual(json.loads(script_json(self.page, "data")), json.loads(text(BUILT, "data.json")))
        self.assertEqual(json.loads(script_json(self.page, "build")), json.loads(text(BUILT, "build.json")))
        self.assertEqual(self.page.count("<script"), 3)

    def test_the_page_holds_the_six_sections_and_the_label(self):
        for sid in SECTIONS:
            self.assertIn(f'<section id="{sid}">', self.page, sid)
        self.assertIn("data as of", self.page)

    def test_only_the_forge_and_the_tracker_are_linked(self):
        hosts = set(re.findall(r"https?://([^/\s\"'<>\\)]+)", self.page))
        self.assertEqual(hosts, FORGE_HOSTS)

    def test_the_page_holds_no_path_of_this_machine(self):
        tmp = Path(_TMP.name)
        home = str(Path.home())
        for local in {str(tmp), str(tmp.resolve()), str(KIT), str(KIT.resolve()), *([home] if len(home) > 3 else [])}:
            self.assertNotIn(local, self.page, local)

    def test_check_accepts_the_page_built_from_the_placeholder_store(self):
        r = run_tool(CHECK, BASE, BUILT / "index.html")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertTrue(one_line(r.stdout))
        self.assertRegex(r.stdout,
                         r"^ok: .*index\.html: 6 sections, 2 JSON blocks, no remote load or script call found\n$")

    def test_check_accepts_the_page_built_from_a_blank_store(self):
        page = text(BUILT_BLANK, "index.html")
        for sid in SECTIONS:
            self.assertIn(f'<section id="{sid}">', page, sid)
        self.assertIn("data as of", page)
        self.assertEqual(json.loads(script_json(page, "data")), json.loads(text(BUILT_BLANK, "data.json")))
        r = run_tool(CHECK, BLANK, BUILT_BLANK / "index.html")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stderr, "")


# ── check.py: the shapes it names ────────────────────────────────────────────────────────────────────────────
class Findings(unittest.TestCase):
    """check.scan on the built page with one thing planted: each shape is named, the clean page is silent."""

    ALLOWED = set(FORGE_HOSTS)
    REMOTE = [
        ('<script src="https://cdn.example/x.js"></script>', '<script src="https://cdn.example/x.js">'),
        ('<script src="x.js"></script>', '<script src="x.js">'),
        ('<script src="//cdn.example/x.js"></script>', "cdn.example/x.js"),
        ('<link rel="stylesheet" href="https://cdn.example/a.css">', '<link href="https://cdn.example/a.css">'),
        ('<link rel="icon" href="a.ico">', '<link href="a.ico">'),
        ('<img src="data:image/gif;base64,R0lGOD">', "<img src="),
        ('<iframe src="about:blank"></iframe>', "<iframe>"),
        ('<object data="x"></object>', "<object>"),
        ('<embed src="x">', "<embed>"),
        ('<form action="https://evil.example/"></form>', "<form action="),
        ('<a href="https://evil.example/">x</a>', "link host not allowed: evil.example"),
        ('<a href="//evil.example/x">x</a>', "link host not allowed: evil.example"),
        ('<a href=" HTTPS://EVIL.EXAMPLE/">x</a>', "link host not allowed: evil.example"),
        ('<a href="ht\ttps://evil.example/">x</a>', "link host not allowed: evil.example"),
        ('<a href="https:\\\\evil.example/">x</a>', "link host not allowed:"),
        ('<a href="https://github.com.evil.example/">x</a>', "link host not allowed: github.com.evil.example"),
        ('<a href="https://github.com@evil.example/">x</a>', "link host not allowed: evil.example"),
        ('<a href="https://evil.example:8443/">x</a>', "link host not allowed: evil.example"),
        ('<div style="background:url(https://evil.example/a.png)">x</div>', "style loads"),
        ('<style>@import "https://evil.example/a.css";</style>', "style loads"),
        ("<style>.a{background:url( '//evil.example/a.png' )}</style>", "style loads"),
        ('<button formaction="https://evil.example/">x</button>', "forbidden attribute: <button formaction="),
        ('<svg><use href="https://evil.example/s.svg#a"/></svg>', "remote reference outside a link"),
    ]
    FINE = [
        '<a href="https://github.com/acme/widgets/pull/7">x</a>',
        '<a href="https://tracker.example/browse/KEY-123">x</a>',
        '<a href="#sessions">x</a>',
        '<a href="notes/page.html">x</a>',
        '<p>https://evil.example/ written as text</p>',
        '<p>fetch( and url( and import( written as text</p>',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'">',
        '<meta name="refresh" content="5">',
        '<div data-id="7" style="color:red;background:#fff">x</div>',
        '<a href="#sessions" title="fetch( url( new Worker">x</a>',
        '<a href="javascript:void(0)">x</a>',
        '<button onclick="toggle(this)">x</button>',
        "<style>.x{color:red;margin:0 auto}</style>",
        "<script>var prefetch = 1, importance = 2; refetch(1); newWorkers(2); var myWebSocketless = 3;</script>",
        '<script id="extra" type="application/json">{"a": "fetch( WebSocket document.write"}</script>',
        '<script id="extra2" type="application/json">{"a": "innerHTML outerHTML insertAdjacentHTML new Audio '
        'window.open location.href el.src = x https://cdn.example/a.js"}</script>',
        "<p>innerHTML, outerHTML, insertAdjacentHTML, new Audio, window.open, location.href and el.src = x as text</p>",
        "<p>see https://cdn.example/a.js or http://cdn.example/b written as text</p>",
        "<!-- innerHTML, window.open, el.src = x and https://cdn.example/a.js are only a comment -->",
        '<!--[if IE]><script>el.innerHTML = 1; go("https://cdn.example/x")</script><![endif]-->',
        "<script>var xinnerHTML = 1, innerHTMLs = 2, xouterHTML = 3, outerHTMLs = 4, reinsertAdjacentHTML = 5, "
        "insertAdjacentHTMLs = 6, newAudio = 7, s = 'renew Audio'; new Audioish();</script>",
        "<script>var e = {}; e.srcElement = 5; e.mysrc = 6; e.source = 7; relocation.href = 8; locations.assign = 9;"
        " location.hrefs = 10; location.assigned = 11; location.replacement = 12;</script>",
        "<script>if (a.src == b) { go(); } if (c.src === d || e.src != f || g.src >= 2 || h.src <= 3) go();</script>",
        "<script>door.open(); x.open(1); reopen(); mywindow.open(); var opener = window.opener;</script>",
        "<script>var h = location.hash.replace(/^#/, ''); var q = location.search, p = location.pathname;</script>",
        "<script>var n = 1; // a plain comment, no address\nvar s = '//', t = 'http', u = 'https:', "
        "v = 'xhttps://', w = 'ahttp://';</script>",
    ]

    @classmethod
    def setUpClass(cls):
        cls.page = text(BUILT, "index.html")

    def scan(self, page: str, sections=SECTIONS, allowed=None) -> list:
        return check_module().scan(page, list(sections), set(self.ALLOWED if allowed is None else allowed))

    def plant(self, markup: str) -> str:
        self.assertEqual(self.page.count("</footer>"), 1)
        return self.page.replace("</footer>", markup + "</footer>")

    def test_the_clean_page_has_no_finding(self):
        self.assertEqual(self.scan(self.page), [])

    def test_a_remote_reference_is_named(self):
        for markup, needle in self.REMOTE:
            with self.subTest(markup=markup):
                findings = self.scan(self.plant(markup))
                self.assertTrue(any(needle in f for f in findings), findings)

    def test_the_forge_the_tracker_and_local_links_pass(self):
        for markup in self.FINE:
            with self.subTest(markup=markup):
                self.assertEqual(self.scan(self.plant(markup)), [])

    def test_the_tracker_link_needs_its_host_allowed(self):
        page = self.plant('<a href="https://tracker.example/browse/KEY-123">x</a>')
        self.assertEqual(self.scan(page), [])
        self.assertTrue(any("tracker.example" in f for f in self.scan(page, allowed={"github.com"})))

    def test_a_missing_section_is_named(self):
        for sid in SECTIONS:
            with self.subTest(section=sid):
                page = self.page.replace(f'id="{sid}"', f'id="{sid}-x"')
                self.assertEqual(self.scan(page), [f"missing section id: {sid}"])

    def test_the_label_counts_only_where_a_reader_sees_it(self):
        label = '<span class="label">data as of</span>'
        self.assertIn(label, self.page)
        gone = self.page.replace(label, '<span class="label">as of</span>')
        self.assertEqual(self.scan(gone), ['missing text: "data as of"'])
        hidden = gone.replace("</footer>", "<style>/* data as of */</style><script>var s = 'data as of';</script></footer>")
        self.assertEqual(self.scan(hidden), ['missing text: "data as of"'])

    def test_the_json_blocks_must_be_there_json_and_an_object(self):
        def data_block(body: str, kind: str = "application/json") -> str:
            return re.sub(r'<script id="data" type="application/json">.*?</script>',
                          lambda m: f'<script id="data" type="{kind}">{body}</script>', self.page, flags=re.S)

        self.assertTrue(any(f.startswith("invalid JSON in #data") for f in self.scan(data_block("{not json"))))
        self.assertTrue(any(f.startswith("invalid JSON in #data") for f in self.scan(data_block(""))))
        self.assertEqual(self.scan(data_block("[1]")), ["JSON block is not an object: #data"])
        self.assertEqual(self.scan(data_block("{}", "text/plain")),
                         ['JSON block is not application/json: #data (type="text/plain")'])
        self.assertEqual(self.scan(self.page.replace('<script id="build"', '<script id="build-x"')),
                         ["missing JSON block: #build"])

    def test_a_finding_is_one_ascii_line_whatever_the_value(self):
        page = self.plant('<script src="https://cdn.example/\u00e9\nx.js">' + "y" * 300 + "</script>")
        findings = self.scan(page)
        self.assertEqual(len(findings), 1)
        for line in findings:
            self.assertTrue(line.isascii() and "\n" not in line and len(line) < 200, line)

    def test_a_repeated_finding_is_reported_once_in_page_order(self):
        bad = '<script src="https://cdn.example/x.js"></script>'
        self.assertEqual(self.scan(self.plant(bad * 3)), self.scan(self.plant(bad)))
        two = self.scan(self.plant(bad + '<a href="https://evil.example/">x</a>'))
        self.assertEqual([f.split(":")[0] for f in two], ["forbidden element", "link host not allowed"])

    def test_a_page_the_parser_cannot_read_is_a_finding_not_a_crash(self):
        for page in ("", "<<<>>>", "<a href", "&#xZZ; <script", "\x00\ufffd", "<script>" * 50):
            with self.subTest(page=page[:20]):
                self.assertTrue(self.scan(page))


# ── check.py: the command ────────────────────────────────────────────────────────────────────────────────────
class Planted(unittest.TestCase):
    """The built page in a scratch dir, a way to plant markup in it, and the command run against the result."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kit-shell-t-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.page = text(BUILT, "index.html")

    def write_page(self, html: str | None = None, name: str = "page.html") -> Path:
        path = self.tmp / name
        path.write_text(self.page if html is None else html, encoding="utf-8")
        return path

    def planted(self, markup: str) -> Path:
        return self.write_page(self.page.replace("</footer>", markup + "</footer>"))

    def check(self, *args, root: Path | None = None, env=None) -> subprocess.CompletedProcess:
        return run_tool(CHECK, BASE if root is None else root, *args, env=env, cwd=self.tmp)


class Cli(Planted):
    def test_a_clean_page_exits_0_with_one_line_and_changes_nothing(self):
        path = self.write_page()
        before = {p.name: p.read_bytes() for p in self.tmp.iterdir()}
        r = self.check(path)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertTrue(one_line(r.stdout))
        self.assertTrue(r.stdout.startswith("ok: "), r.stdout)
        self.assertEqual({p.name: p.read_bytes() for p in self.tmp.iterdir()}, before)

    def test_a_script_src_and_a_stray_link_exit_1_and_name_the_hit(self):
        r = self.check(self.planted('<script src="https://cdn.example/x.js"></script>'))
        self.assertEqual(r.returncode, 1)
        self.assertIn("cdn.example/x.js", r.stdout)
        r = self.check(self.planted('<a href="https://evil.example/">x</a>'))
        self.assertEqual(r.returncode, 1)
        self.assertIn("evil.example", r.stdout)

    def test_findings_go_to_stdout_one_per_line_and_the_count_to_stderr(self):
        one = self.check(self.planted('<a href="https://evil.example/">x</a>'))
        self.assertEqual(len(one.stdout.splitlines()), 1)
        self.assertTrue(one_line(one.stderr))
        self.assertRegex(one.stderr, r"^check\.py: .*page\.html: 1 finding\n$")
        two = self.check(self.planted('<a href="https://evil.example/">x</a><embed src="x">'))
        self.assertEqual(len(two.stdout.splitlines()), 2)
        self.assertRegex(two.stderr, r"^check\.py: .*page\.html: 2 findings\n$")

    def test_the_tracker_host_comes_from_the_store_the_check_runs_against(self):
        path = self.planted('<a href="https://tracker.example/browse/KEY-123">x</a>')
        r = self.check(path, root=BASE)
        self.assertEqual((r.returncode, r.stdout.startswith("ok: ")), (0, True), r.stdout + r.stderr)
        r = self.check(path, root=BLANK)  # no tracker.url_template there: the host is not the store's
        self.assertEqual(r.returncode, 1)
        self.assertIn("tracker.example", r.stdout)
        self.assertTrue(one_line(r.stderr), r.stderr)

    def test_the_forge_is_allowed_with_no_store_at_all(self):
        path = self.planted('<a href="https://github.com/acme/widgets/issues/42">x</a>')
        env = engine_env(self.tmp / "no-such" / ".context")
        r = self.check(path, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stderr, "")

    def test_allow_host_adds_a_host_by_name_or_by_url(self):
        path = self.planted('<a href="https://evil.example/x">x</a>')
        for value in ("evil.example", "EVIL.example", "https://evil.example/any/path"):
            with self.subTest(value=value):
                self.assertEqual(self.check(path, "--allow-host", value).returncode, 0)
        self.assertEqual(self.check(path, "--allow-host", "other.example").returncode, 1)
        both = self.check(path, "--allow-host", "other.example", "--allow-host", "evil.example")
        self.assertEqual(both.returncode, 0)

    def test_a_missing_section_exits_1_and_sections_narrows_or_widens_the_list(self):
        path = self.write_page(self.page.replace('id="queues"', 'id="queues-x"'))
        r = self.check(path)
        self.assertEqual((r.returncode, r.stdout), (1, "missing section id: queues\n"))
        self.assertEqual(self.check(path, "--sections", "sessions,cost").returncode, 0)
        self.assertEqual(self.check(path, "--sections", "sessions", "--sections", "cost, pointers").returncode, 0)
        wider = self.check(self.write_page(), "--sections", "sessions,nope")
        self.assertEqual((wider.returncode, wider.stdout), (1, "missing section id: nope\n"))

    def test_an_unparsable_data_block_exits_1(self):
        broken = re.sub(r'(<script id="data" type="application/json">).*?(</script>)',
                        lambda m: m.group(1) + "{not json" + m.group(2), self.page, flags=re.S)
        r = self.check(self.write_page(broken))
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("invalid JSON in #data"), r.stdout)

    def test_a_file_that_is_not_there_exits_2_with_one_line(self):
        for target in (self.tmp / "absent.html", self.tmp):  # a missing file, then a directory
            with self.subTest(target=target.name):
                r = self.check(target)
                self.assertEqual((r.returncode, r.stdout), (2, ""))
                self.assertTrue(one_line(r.stderr), r.stderr)
                self.assertTrue(r.stderr.startswith("check.py: cannot read "), r.stderr)

    def test_a_usage_error_exits_2_with_one_line(self):
        page = str(self.write_page())
        for args in ([], ["--bogus", page], ["--sections", "", page], ["--sections", "a b", page],
                     ["--sections", "1x", page], ["--allow-host", "", page], [page, "extra"]):
            with self.subTest(args=args):
                r = self.check(*args)
                self.assertEqual((r.returncode, r.stdout), (2, ""))
                self.assertTrue(one_line(r.stderr), r.stderr)
                self.assertTrue(r.stderr.startswith("check.py: "), r.stderr)

    def test_help_needs_no_store_and_no_environment_value(self):
        r = subprocess.run([sys.executable, str(CHECK), "--help"], capture_output=True, text=True, encoding="utf-8",
                           timeout=60, cwd=str(self.tmp), env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        for needle in ("--allow-host", "--sections", "Exit 0", "exit 2"):
            self.assertIn(needle, r.stdout)


# ── check.py: one hostile construct at a time ────────────────────────────────────────────────────────────────
class Constructs(Planted):
    """The command on the built page with one construct planted: it exits 1 and the one finding names the construct.
    The shipped page exiting 0 (RealTemplate) is the other half of each of these."""

    def verdict(self, path: Path, needle: str, count: int = 1) -> None:
        r = self.check(path)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(needle, r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), count, r.stdout)
        self.assertTrue(one_line(r.stderr), r.stderr)

    def refuses(self, markup: str, needle: str, count: int = 1) -> None:
        with self.subTest(markup=markup):
            self.verdict(self.planted(markup), needle, count)

    # the page redirects itself
    def test_a_meta_refresh_is_refused_with_or_without_a_url(self):
        for markup in ('<meta http-equiv="refresh" content="0; url=https://evil.example/">',
                       '<META HTTP-EQUIV=" Refresh " CONTENT="30">',
                       "<meta http-equiv=refresh content='0;url=/next.html'>"):
            self.refuses(markup, 'meta refresh: <meta http-equiv="refresh"')

    # an attribute that fetches or navigates
    def test_an_img_srcset_is_refused_when_the_remote_candidate_is_not_first(self):
        self.refuses('<img srcset="a.png 1x, https://evil.example/b.png 2x">', "forbidden attribute: <img srcset=")

    def test_a_link_imagesrcset_is_refused(self):
        self.refuses('<link rel="preload" as="image" imagesrcset="a.png 1x, //evil.example/b.png 2x">',
                     "forbidden attribute: <link imagesrcset=")

    def test_a_source_srcset_is_refused(self):
        self.refuses('<picture><source srcset="//evil.example/b.webp"></picture>',
                     "forbidden attribute: <source srcset=")

    def test_a_poster_is_refused_remote_or_not(self):
        self.refuses('<video poster="https://evil.example/p.png"></video>', "forbidden attribute: <video poster=")
        self.refuses('<video poster="p.png"></video>', "forbidden attribute: <video poster=")

    def test_a_ping_list_is_refused_when_the_remote_url_is_not_first(self):
        self.refuses('<a href="#sessions" ping="/a https://evil.example/p">x</a>', "forbidden attribute: <a ping=")

    def test_a_data_attribute_is_refused(self):
        self.refuses('<div data="d.json">x</div>', "forbidden attribute: <div data=")

    def test_an_action_or_a_formaction_is_refused_on_any_element(self):
        self.refuses('<button formaction="/save">x</button>', "forbidden attribute: <button formaction=")
        self.refuses('<input type="submit" formaction="//evil.example/s">', "forbidden attribute: <input formaction=")
        self.refuses('<form action="/save"></form>', "<form action=")
        self.refuses('<div action="/save">x</div>', "forbidden attribute: <div action=")

    # CSS that loads
    def test_a_url_in_a_style_block_is_refused_remote_or_not(self):
        self.refuses("<style>.a{background:url(a.png)}</style>", 'style loads: "url(a.png)" in <style>')
        self.refuses("<style>.a{background:URL( 'https://evil.example/a.png' )}</style>", "style loads: ")

    def test_an_image_set_in_a_style_block_is_refused(self):
        self.refuses('<style>.a{background-image:image-set("a.png" 1x)}</style>', "style loads: \"image-set(")
        self.refuses('<style>.a{background-image:-webkit-image-set("a.png" 1x)}</style>', "style loads: \"image-set(")

    def test_a_url_in_a_style_attribute_is_refused_remote_or_not(self):
        self.refuses('<div style="background:url(a.png)">x</div>', 'style loads: "url(a.png)" in a style attribute')
        self.refuses('<div style="background:url(//evil.example/a.png)">x</div>', "in a style attribute")

    def test_an_image_set_in_a_style_attribute_is_refused(self):
        self.refuses("<div style=\"background:image-set('a.png' 1x)\">x</div>", "style loads: \"image-set(")

    # script that opens a connection, loads code or writes markup
    def test_a_fetch_call_is_refused(self):
        for call in ("fetch('/x')", "window.fetch ('/x')", "fetch\n('/x')"):
            self.refuses(f"<script>{call}</script>", "script call: fetch( in an inline script")

    def test_an_xmlhttprequest_is_refused(self):
        self.refuses("<script>var r = new XMLHttpRequest(); r.open('GET', '/x');</script>",
                     "script call: XMLHttpRequest in an inline script")

    def test_a_sendbeacon_call_is_refused(self):
        self.refuses("<script>navigator.sendBeacon('/x', 'y')</script>", "script call: sendBeacon in an inline script")

    def test_a_websocket_is_refused(self):
        self.refuses("<script>new WebSocket('wss://evil.example/')</script>",
                     "script call: WebSocket in an inline script")

    def test_an_eventsource_is_refused(self):
        self.refuses("<script>new EventSource('/s')</script>", "script call: EventSource in an inline script")

    def test_a_dynamic_import_is_refused(self):
        for call in ("import('./m.js')", "import ('./m.js')"):
            self.refuses(f"<script>{call}</script>", "script call: import( in an inline script")

    def test_a_worker_is_refused(self):
        for call in ("new Worker('w.js')", "new  SharedWorker('w.js')"):
            self.refuses(f"<script>{call}</script>", "script call: new Worker in an inline script")

    def test_a_service_worker_is_refused(self):
        self.refuses("<script>navigator.serviceWorker.register('/sw.js')</script>",
                     "script call: serviceWorker in an inline script")

    def test_a_document_write_is_refused(self):
        for call in ("document.write('<p>x</p>')", "document . writeln('x')"):
            self.refuses(f"<script>{call}</script>", "script call: document.write in an inline script")

    def test_an_image_beacon_and_import_scripts_are_refused(self):
        self.refuses("<script>var i = new Image(); go(i)</script>", "script call: new Image in an inline script")
        self.refuses("<script>importScripts('/w.js')</script>", "script call: importScripts in an inline script")

    def test_an_image_beacon_trips_the_constructor_and_the_src_assignment(self):
        r = self.check(self.planted("<script>new Image().src = '/x'</script>"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["script call: new Image in an inline script",
                                                 "script call: .src = in an inline script"])

    # script that writes markup, navigates or names a remote address
    def test_an_innerhtml_access_is_refused(self):
        for code in ("el.innerHTML = '<b>x</b>'", "document.body.innerHTML += x", "var h = el.innerHTML"):
            self.refuses(f"<script>{code}</script>", "script call: innerHTML in an inline script")

    def test_an_outerhtml_access_is_refused(self):
        for code in ("el.outerHTML = '<b>x</b>'", "el['outerHTML'] = x"):
            self.refuses(f"<script>{code}</script>", "script call: outerHTML in an inline script")

    def test_an_insertadjacenthtml_call_is_refused(self):
        for code in ("el.insertAdjacentHTML('beforeend', '<b>x</b>')", "el.insertAdjacentHTML ('afterbegin', x)"):
            self.refuses(f"<script>{code}</script>", "script call: insertAdjacentHTML in an inline script")

    def test_a_new_audio_is_refused(self):
        for code in ("new Audio('/a.mp3')", "new  Audio()", "new\nAudio('/a.mp3')"):
            self.refuses(f"<script>{code}</script>", "script call: new Audio in an inline script")

    def test_a_window_open_is_refused(self):
        for code in ("window.open('/x')", "window . open('/x', '_blank')", "window\n.open('/x')"):
            self.refuses(f"<script>{code}</script>", "script call: window.open in an inline script")

    def test_a_location_navigation_is_refused(self):
        for code in ("location.href = '/x'", "window.location.assign('/x')", "document.location.replace('/x')",
                     "location . href='#a'"):
            self.refuses(f"<script>{code}</script>", "script call: location.href|assign|replace in an inline script")

    def test_a_src_assignment_is_refused(self):
        for code in ("img.src = '/x.png'", "s.src='/x.js'", "el . src =x", "el.\nsrc\n= x"):
            self.refuses(f"<script>{code}</script>", "script call: .src = in an inline script")

    def test_a_literal_remote_url_in_a_script_is_refused(self):
        for code in ("var u = 'https://cdn.example/x.js'", 'var u = "http://cdn.example/"',
                     "u = 'HTTPS://CDN.EXAMPLE/x'", "// see https://cdn.example/docs\nvar n = 1"):
            self.refuses(f"<script>{code}</script>", "script call: http(s):// in an inline script")

    NEW_FORMS = (
        ("innerHTML", "el.innerHTML = 'x'"),
        ("outerHTML", "el.outerHTML = 'x'"),
        ("insertAdjacentHTML", "el.insertAdjacentHTML('beforeend', 'x')"),
        ("new Audio", "new Audio('a.mp3')"),
        ("window.open", "window.open('a.html')"),
        ("location.href|assign|replace", "location.href = 'a.html'"),
        (".src =", "el.src = 'a.png'"),
        ("http(s)://", "go('https://cdn.example/a.js')"),
    )

    def test_each_new_form_is_read_in_a_script_an_event_handler_and_a_javascript_url(self):
        for name, code in self.NEW_FORMS:
            self.refuses(f"<script>{code}</script>", f"script call: {name} in an inline script")
            self.refuses(f'<button onclick="{code}">x</button>', f"script call: {name} in the onclick attribute")
            self.refuses(f'<a href="javascript:{code}">x</a>', f"script call: {name} in the href attribute")

    def test_a_module_or_a_speculation_rules_script_is_refused(self):
        self.refuses('<script type="module">import x from "./m.js";</script>',
                     'forbidden script type: <script type="module">')
        self.refuses('<script type="speculationrules">{"prefetch": [{"source": "list", "urls": ["/n"]}]}</script>',
                     'forbidden script type: <script type="speculationrules">')

    def test_an_event_handler_attribute_is_scanned_like_a_script(self):
        self.refuses("<button onclick=\"fetch('/x')\">x</button>", "script call: fetch( in the onclick attribute")

    def test_a_javascript_url_is_scanned_like_a_script(self):
        for href in ("javascript:fetch('/x')", "JavaScript:fetch('/x')", "java\nscript:fetch('/x')"):
            self.refuses(f'<a href="{href}">x</a>', "script call: fetch( in the href attribute")

    # what is read, and what is not
    def test_a_style_or_a_script_left_open_at_the_end_of_the_page_is_still_read(self):
        for tail, needle in (("<style>@import url(//evil.example/a.css);", "style loads: "),
                             ("<script>fetch('/x')", "script call: fetch( in an inline script")):
            with self.subTest(tail=tail):
                self.verdict(self.write_page(self.page.rstrip() + tail), needle)

    def test_the_words_inside_the_json_data_blocks_are_data_and_not_script(self):
        words = ('{"title": "fetch( XMLHttpRequest sendBeacon WebSocket import( new Worker document.write url( '
                 'innerHTML outerHTML insertAdjacentHTML new Audio window.open location.href el.src = x '
                 'https://cdn.example/a.js"}')
        page = re.sub(r'(<script id="data" type="application/json">).*?(</script>)',
                      lambda m: m.group(1) + words + m.group(2), self.page, flags=re.S)
        self.assertIn(words, page)
        r = self.check(self.write_page(page))
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)

    def test_a_json_block_declared_as_javascript_is_read_as_script(self):
        page = self.page.replace('<script id="data" type="application/json">',
                                 '<script id="data" type="text/javascript">fetch("/x");')
        r = self.check(self.write_page(page))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("script call: fetch( in an inline script", r.stdout)


# ── the commands the skill and the heartbeat run, end to end ─────────────────────────────────────────────────
@unittest.skipUnless(shutil.which("make"), "make is not installed")
class MakeFlow(unittest.TestCase):
    """`make -s -C context-db mission-*` against a copy of the placeholder store with the kit's own page: the lines
    the skill keys on are the lines these commands print."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kit-shell-m-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / ".context"
        shutil.copytree(BASE, self.root)
        self.page = self.root / "state" / "mission-control" / "index.html"

    def make(self, target: str, *args, root: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["make", "-s", "--no-print-directory", "-C", str(ENGINE), target, *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=120,
                              env=engine_env(root or self.root), cwd=str(self.tmp))

    def test_detect_build_open_check(self):
        before = self.make("mission-diff")
        self.assertNotEqual(before.returncode, 0)
        self.assertRegex(before.stdout, r"(?m)^mission: not built ")
        self.assertIn("Error 1", before.stderr)

        first = self.make("mission-build")
        self.assertEqual(first.returncode, 0, first.stderr)
        line = re.search(r"(?m)^mission: built (\S+) ([0-9a-f]{12})$", first.stdout)
        self.assertIsNotNone(line, first.stdout)
        self.assertEqual(Path(line.group(1)).resolve(), self.page.resolve())
        self.assertEqual(text(self.page.parent, ".gitignore").strip(), "*")

        current = self.make("mission-diff")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertRegex(current.stdout, rf"(?m)^mission: unchanged {line.group(2)} ")

        opened = self.make("mission-open")
        self.assertEqual(opened.returncode, 0, opened.stderr)
        shown = re.search(r"(?m)^(\S*index\.html)$", opened.stdout)  # by its shape: other lines may surround it
        self.assertIsNotNone(shown, opened.stdout)
        self.assertEqual(Path(shown.group(1)).resolve(), self.page.resolve())

        verdict = run_tool(CHECK, self.root, self.page)
        self.assertEqual(verdict.returncode, 0, verdict.stdout + verdict.stderr)

        again = self.make("mission-build")
        self.assertRegex(again.stdout, rf"(?m)^mission: unchanged {line.group(2)} ")
        forced = self.make("mission-build", "FORCE=1")
        self.assertRegex(forced.stdout, rf"(?m)^mission: built \S+ {line.group(2)}$")

    def test_the_output_does_not_depend_on_how_the_suite_was_started(self):
        """An outer `make test` exports MAKEFLAGS (w: print the directory, even under -s); the page line still stands
        alone and a failure still ends in make's own Error line."""
        with mock.patch.dict(os.environ, {"MAKEFLAGS": "w", "MFLAGS": "-w", "MAKELEVEL": "1"}):
            built = self.make("mission-build")
            opened = self.make("mission-open")
            refused = self.make("mission-diff", root=self.tmp / "no-such" / ".context")
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assertEqual(Path(opened.stdout.strip()).resolve(), self.page.resolve())
        for r in (built, opened, refused):
            self.assertNotIn("directory", r.stdout + r.stderr)
        self.assertIn("Error 2", refused.stderr)

    def test_a_changed_store_makes_the_page_behind(self):
        self.assertEqual(self.make("mission-build").returncode, 0)
        plant(self.root, "reference/priorities.md", "Ship the widget exporter", "Ship the widget importer")
        behind = self.make("mission-diff")
        self.assertNotEqual(behind.returncode, 0)
        self.assertRegex(behind.stdout, r"(?m)^mission: changed [0-9a-f]{12} -> [0-9a-f]{12}")
        self.assertIn("Error 1", behind.stderr)
        self.assertRegex(self.make("mission-build").stdout, r"(?m)^mission: built ")
        self.assertRegex(self.make("mission-diff").stdout, r"(?m)^mission: unchanged ")

    def test_a_store_that_is_not_there_stops_with_error_2(self):
        r = self.make("mission-diff", root=self.tmp / "no-such" / ".context")
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(r.stderr, r"(?m)^mission: content root not found: ")
        self.assertIn("Error 2", r.stderr)

    def test_a_leak_hit_names_section_and_line_and_writes_nothing(self):
        plant(self.root, "reference/priorities.md", "Ship the widget exporter", f"Ship the widget exporter {EMAIL}")
        r = self.make("mission-build")
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(r.stdout, r"(?m)^priorities · line \d+ · e-mail address · ")
        self.assertIn("Error 1", r.stderr)
        self.assertFalse(self.page.parent.exists())
        self.assertNotIn(EMAIL, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
