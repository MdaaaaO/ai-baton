"""kit-health.py's deterministic helpers and the shared leak shapes: separated streams, no token exemption,
tracker-aware ticket shape, anchored allow-list, loader errors reported, a plain run that never writes the DB.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(KIT / "context-db"))

import kb  # noqa: E402
import kit_profile  # noqa: E402
import leak_shapes  # noqa: E402

from tests import hermetic_env  # noqa: E402

LEAK = "C0" + "AB12CD3EF"  # a Slack-shaped id, assembled so no scanner reads this file as a leak
TICKET = "DATA-" + "1234"  # a ticket-shaped key, likewise
PROJ = "PROJ-" + "42"


def load_kit_health():
    spec = importlib.util.spec_from_file_location("kit_health_under_test", KIT / "skills" / "kit-health" / "kit-health.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class Shapes(unittest.TestCase):
    def hits(self, text: str, **kw) -> list[str]:
        return [what for _n, what, _hit in leak_shapes.scan(text, shapes=leak_shapes.shapes(**kw))]

    def test_ticket_shape_ignores_acronyms_and_week_numbers(self):
        for s in ("AES-256", "RSA-2048", "ARM-64", "W37-2026", "SHA-256", "HMAC-256", "GPT-40", "UTF-16"):
            self.assertEqual(self.hits(s), [], s)
        self.assertEqual(self.hits(f"see {TICKET}"), ["ticket key"])
        self.assertEqual(self.hits(f"see {PROJ}"), ["ticket key"])
        self.assertEqual(self.hits("see KEY-123"), [])  # the docs' placeholder key stays exempt, as before

    def test_tracker_aware_shapes(self):
        # the generic shape stays on every tracker (a key from ANOTHER environment is the leak a shared kit risks);
        # a Jira-style tracker's own key_regex is ADDED as a second shape, never swapped in
        self.assertEqual(self.hits(f"see {TICKET}", tracker_kind="github"), ["ticket key"])
        self.assertEqual(self.hits(f"see {TICKET}", tracker_kind="jira", key_regex=r"\b(PROJ-\d+)\b"), ["ticket key"])
        own = "PROJ-" + "7"  # fits the regex but not the generic shape (one digit)
        self.assertEqual(self.hits(f"see {own}", tracker_kind="jira", key_regex=r"\b(PROJ-\d+)\b"), ["ticket key (tracker.key_regex)"])
        self.assertEqual(self.hits(f"see {own}", tracker_kind="github", key_regex=r"\b(PROJ-\d+)\b"), [])  # GitHub: no regex added
        self.assertEqual(self.hits(f"see {TICKET}", tracker_kind="jira", key_regex=r"\b(PROJ-\d+"), ["ticket key"])  # invalid: nothing added
        self.assertEqual(len(leak_shapes.shapes()), len(leak_shapes.LEAK_SHAPES))
        self.assertEqual(len(leak_shapes.shapes("jira", r"\b(PROJ-\d+)\b")), len(leak_shapes.LEAK_SHAPES) + 1)

    def test_a_key_fitting_both_ticket_shapes_is_one_hit(self):
        # both scanners stop at the first shape per line, so a key the generic shape AND tracker.key_regex match
        # is one row (the generic one), never two
        hits = leak_shapes.scan(f"see {PROJ}\n", shapes=leak_shapes.shapes("jira", r"\b(PROJ-\d+)\b"))
        self.assertEqual(hits, [(1, "ticket key", PROJ)])

    def test_no_token_exempts_a_line(self):
        # the bare `kit-health` token used to exempt every line mentioning the skill
        kh = load_kit_health()
        self.assertIs(kh.SKIP_LINE, leak_shapes.SKIP_LINE)
        self.assertEqual(len(leak_shapes.scan(f"kit-health posts to {LEAK}\n")), 1)
        self.assertEqual(leak_shapes.scan(f'  facts: "slack.channel {LEAK}"\n'), [])

    def test_allow_list_is_anchored(self):
        # `README.md:acme` must not allow `docs/README.md:acme…`; a prefix entry still allows its own path
        pats = (re.compile("skills/x/SKILL.md:C0"), re.compile(r"docs/a\.md:acme1$"))
        self.assertTrue(leak_shapes.is_allowed("skills/x/SKILL.md", LEAK, pats))
        self.assertFalse(leak_shapes.is_allowed("docs/skills/x/SKILL.md", LEAK, pats))
        self.assertTrue(leak_shapes.is_allowed("docs/a.md", "acme1", pats))
        self.assertFalse(leak_shapes.is_allowed("docs/a.md", "acme12", pats))

    def test_real_number_behind_a_placeholder_prefix(self):
        # #92: `KEY-` / `ABC-` are exempt from the ticket-key shape, so a real number behind them needs its own shape
        for line in ("see KEY-" + "9876 for why", "ABC-" + "77 did it", "file key-" + "9876-foo-bar.md", "topic key-" + "9876-fix"):
            with self.subTest(line=line):
                self.assertTrue(self.hits(line), line)
        for line in ("see KEY-123 and KEY-456", "ABC-123", "file key-123-<slug>.md", "KEY-123-commit-msg.txt", "topic key-123-fix"):
            with self.subTest(line=line):
                self.assertEqual(self.hits(line), [], line)

    def test_install_specific_mcp_ids_and_sandbox_cli(self):
        # #94: a concrete connector tool id or a sandbox product's CLI name is a hit; the placeholder form is not
        for line in ("tools: Read, mcp__claude_" + "ai_Acme_Jira_2__getIssue", "run it in " + "sb" + "x first"):
            with self.subTest(line=line):
                self.assertTrue(self.hits(line), line)
        for line in ("tools: `mcp__<server>__<tool>`", "mcp__github_inline_comment__create_inline_comment", "sbxtool", "a sandbox shell"):
            with self.subTest(line=line):
                self.assertEqual(self.hits(line), [], line)

    def test_third_party_attribution(self):
        # #95: a colleague's or former employer's repo named as the source is a hit; a placeholder or a plain mention is not
        for line in ("adapted from a colle" + "ague's `widgets` repo", "ported from my former emp" + "loyer's acme/tools",
                     "a team" + "mate\u2019s `x`"):
            with self.subTest(line=line):
                self.assertTrue(self.hits(line), line)
        for line in ("adapted from a colleague's `<repo>`", "ask a colleague for review", "the teammate who owns it",
                     "colleagues' logins stay in the env store"):
            with self.subTest(line=line):
                self.assertEqual(self.hits(line), [], line)

    def kinds(self, text: str) -> list[str]:
        return [what.split(" (")[0] for what in self.hits(text)]

    def test_universal_sandbox_wording_shape(self):
        # a sandbox stated as the universe is a leak of one machine; a conditional or the config key is not
        sb = "sand" + "box"  # assembled so this file never reads as a hit itself
        for s in (f"This {sb} cannot run it", f"The {sb} has no browser", f"works from the {sb}", f"shared by every session on this {sb}",
                  f"the {sb} token lacks the scope", f"a variable that is {sb}-only", f"the {sb} lacks a keyring", f"{sb} can't sign"):
            self.assertEqual(self.kinds(s), ["universal sandbox wording"], s)
        for s in (f"where gh works through a token-injecting proxy (a {sb}), the token", f"github.{sb}_token_prefix",
                  f"a {sb} recreate wipes ~/.claude", f"a {sb} whose proxy injects credentials", f"{sb}ed processes"):
            self.assertEqual(self.kinds(s), [], s)

    def test_sandbox_only_path_and_variable_shapes(self):
        # the mount path and the per-job directory variable exist on one kind of machine only — any expansion of the
        # variable is a hit now (before: only a path under it), a read through os.environ in the resolver is not
        sb = "sand" + "box"
        self.assertEqual(self.kinds("check for " + "/run/" + sb + "/source"), [f"{sb}-only path"])
        self.assertEqual(self.kinds("`" + "/run/" + sb + "`"), [f"{sb}-only path"])
        var = "CLAUDE_JOB" + "_DIR"
        self.assertEqual(self.kinds(f"`${var}` is unset on a host"), [f"{sb}-only variable"])
        self.assertEqual(self.kinds("${" + var + "}/tmp/x"), [f"{sb}-only variable"])
        self.assertEqual(self.kinds(f'os.environ.get("{var}")'), [])
        self.assertEqual(self.kinds("a path like /run/lock or /run/user/1000"), [])

    def test_memory_note_pointer_shapes(self):
        # every form a pointer to a personal note took in the kit; the harness auto-memory feature named generically is not one
        note = "some-" + "note"
        mn, mem = "memory " + "note", "Memor" + "ies:"  # the pointer forms themselves, assembled so this file is no hit
        for s in (f"(memory `{note}`)", f"in the {mn} `{note}.md`", f"{mem} `{note}`, `other`", f"live in the {mn}",
                  f"see the {mn}s for the history", mem):
            self.assertEqual(self.kinds(s), ["memory-note pointer"], s)
        for s in ("If you added a memory note, confirm it has a `MEMORY.md` line", "the `MEMORY.md` index if a memory note changed",
                  "auto-memory in `.context/memory/`", "never type a profile from memory into `kb set`", "Memory notes live in .context"):
            self.assertEqual(self.kinds(s), [], s)

    def test_mcp_backed_is_a_subset_of_systems(self):
        # kit-health reads the MCP-backed list from kb.py, beside the flag list it must stay a subset of
        self.assertTrue(set(kb.MCP_BACKED) <= set(kb.SYSTEMS), kb.MCP_BACKED)
        self.assertNotIn("aws_sso", kb.MCP_BACKED)
        self.assertNotIn("lattice", kb.MCP_BACKED)

    def test_allow_txt_ships_no_login_or_readme_entry(self):
        # the kit's own repo name is exempt by kit_repo()/kit_dependencies(); allow.txt carries no identity
        lines = [ln.split("#", 1)[0].strip() for ln in (KIT / "skills" / "kit-health" / "allow.txt").read_text(encoding="utf-8").splitlines()]
        self.assertFalse([ln for ln in lines if ln.startswith("README.md:")], lines)


class Helpers(unittest.TestCase):
    def test_sh_keeps_stdout_and_stderr_apart(self):
        kh = load_kit_health()
        rc, out, err = kh.sh([sys.executable, "-c", "import sys; print('summary'); print('~ stale: x', file=sys.stderr)"])
        self.assertEqual((rc, out, err), (0, "summary", "~ stale: x"))
        rc, out, err = kh.sh(["/no/such/binary"])
        self.assertEqual((rc, out), (127, ""))
        self.assertTrue(err)
        self.assertEqual(kh.both("a", ""), "a")
        self.assertEqual(kh.both("a", "b"), "a\nb")

    def test_loader_failure_is_an_error_line_not_silence(self):
        kh = load_kit_health()
        saved = kh.kb.all_facts
        kh.kb.all_facts = lambda: (_ for _ in ()).throw(ValueError("bad table"))
        try:
            pats, errors = kh.configured_values()
        finally:
            kh.kb.all_facts = saved
        self.assertTrue(any("env-store tables could not be read (bad table)" in e for e in errors), errors)
        self.assertIsInstance(pats, list)


class GitIgnoredScan(unittest.TestCase):
    """`git_ignored()` — the fallback that drops a git-ignored vendored/build tree from `scan_files()` beyond the
    fixed `SKIP_DIRS` list (`node_modules` there catches the common case; this catches any other one this
    environment's own `.gitignore` names)."""

    def test_reports_the_paths_the_gitignore_excludes(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=hermetic_env(root))
            (root / "vendor").mkdir()
            (root / "vendor" / "x.js").write_text("x", encoding="utf-8")
            (root / "kept.md").write_text("x", encoding="utf-8")
            (root / ".gitignore").write_text("vendor/\n", encoding="utf-8")
            self.assertEqual(kh.git_ignored(root, ["vendor/x.js", "kept.md"]), {"vendor/x.js"})

    def test_falls_back_to_empty_without_a_git_checkout(self):
        # a plugin install has no `.git` — check-ignore has nothing to compare against, so scan_files() keeps
        # scanning everything SKIP_DIRS/SKIP_FILES don't already exclude, exactly as before this existed
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.md").write_text("x", encoding="utf-8")
            self.assertEqual(kh.git_ignored(root, ["x.md"]), set())

    def test_empty_input_short_circuits(self):
        kh = load_kit_health()
        self.assertEqual(kh.git_ignored(KIT, []), set())

    def test_a_parent_repo_that_ignores_the_kit_never_empties_the_scan(self):
        # the kit has no .git of its own and sits inside a repo whose .gitignore excludes it: that parent's rules
        # are not the kit's, and honouring them would drop every kit file and read as a clean GREEN scan
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=parent, check=True, env=hermetic_env(parent))
            (parent / ".gitignore").write_text("kit/\n", encoding="utf-8")
            root = parent / "kit"
            root.mkdir()
            (root / "SKILL.md").write_text("x", encoding="utf-8")
            with mock.patch.dict(os.environ, hermetic_env(parent)):
                self.assertEqual(kh.git_ignored(root, ["SKILL.md"]), set())


class AutoCompactCheck(unittest.TestCase):
    """`autocompact_wiring()` (#398): the real backstop is the user's `autoCompactWindow` setting in
    `~/.claude/settings.json`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW` for a cloud session — never the
    plugin's own settings.json (a no-op there). Unset is a WARN, set (either way) is OK."""

    def test_unset_everywhere_warns(self):
        kh = load_kit_health()
        r = kh.Report()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with mock.patch.object(kh.Path, "home", return_value=home), \
                 mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
                kh.autocompact_wiring(r)
        text = "\n".join(r.lines)
        self.assertEqual(r.counts[kh.WARN], 1)
        self.assertIn("auto-compact backstop unset", text)

    def test_user_settings_json_set_is_ok(self):
        kh = load_kit_health()
        r = kh.Report()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".claude").mkdir(parents=True)
            (home / ".claude" / "settings.json").write_text(json.dumps({"autoCompactWindow": 200000}), encoding="utf-8")
            with mock.patch.object(kh.Path, "home", return_value=home), \
                 mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
                kh.autocompact_wiring(r)
        text = "\n".join(r.lines)
        self.assertEqual(r.counts[kh.WARN], 0)
        self.assertIn("autoCompactWindow", text)

    def test_cloud_env_var_set_is_ok_without_reading_settings(self):
        kh = load_kit_health()
        r = kh.Report()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)  # no .claude/settings.json at all — the env var alone must suffice
            with mock.patch.object(kh.Path, "home", return_value=home), \
                 mock.patch.dict(os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "300000"}):
                kh.autocompact_wiring(r)
        text = "\n".join(r.lines)
        self.assertEqual(r.counts[kh.WARN], 0)
        self.assertIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", text)


class DiskCheck(unittest.TestCase):
    """`disk_wiring()` — free-disk on `/`, `$HOME` and the scratch root, plus the top-cache sizes above
    `DISK_WARN_PCT` (a sandbox root overlay filled silently from an untended build cache; the first symptom was
    an ENOSPC inside an unrelated skill step, so kit-health looks at the machine, not only at the kit)."""

    def test_human_size(self):
        kh = load_kit_health()
        self.assertEqual(kh.human_size(0), "0B")
        self.assertEqual(kh.human_size(512), "512B")
        self.assertEqual(kh.human_size(2048), "2.0K")
        self.assertEqual(kh.human_size(5 * 1024 * 1024), "5.0M")

    def test_du_sums_file_sizes_recursively(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.o").write_bytes(b"x" * 100)
            (root / "sub").mkdir()
            (root / "sub" / "b.o").write_bytes(b"y" * 50)
            self.assertEqual(kh.du(root), 150)

    def test_below_threshold_is_ok_and_lists_no_caches(self):
        kh = load_kit_health()
        r = kh.Report()
        Usage = type(kh.shutil.disk_usage(Path.cwd()))  # the real namedtuple type, any real path will do
        with mock.patch.object(kh.shutil, "disk_usage", return_value=Usage(total=100, used=50, free=50)):
            kh.disk_wiring(r, targets=[("/", Path("/"))])
        text = "\n".join(r.lines)
        self.assertIn("✅", text)
        self.assertIn("50% full on `/`", text)
        self.assertEqual(r.counts[kh.WARN], 0)

    def test_above_threshold_warns_and_lists_cache_sizes_once(self):
        kh = load_kit_health()
        r = kh.Report()
        Usage = type(kh.shutil.disk_usage(Path.cwd()))
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".cache" / "go-build").mkdir(parents=True)
            (home / ".cache" / "go-build" / "b.o").write_bytes(b"x" * 4096)
            (home / ".npm").mkdir()  # present but empty — no size, no line
            with mock.patch.object(kh.shutil, "disk_usage", return_value=Usage(total=100, used=96, free=4)), \
                 mock.patch.object(kh.Path, "home", return_value=home):
                kh.disk_wiring(r, targets=[("/", Path("/a")), ("$HOME", Path("/b"))])
        text = "\n".join(r.lines)
        self.assertEqual(r.counts[kh.WARN], 2)  # both targets over DISK_WARN_PCT
        self.assertIn("96% full on `/`", text)
        self.assertIn("96% full on `$HOME`", text)
        self.assertIn("go-build", text)
        self.assertIn("rm -rf ~/.cache/go-build", text)
        self.assertEqual(text.count("safe to remove"), 1)  # listed once, not once per over-threshold target
        self.assertNotIn("~/.npm`", text)  # empty cache dir: no size to report, no line at all


class ReadOnlyRun(unittest.TestCase):
    def engine_section(self, root: Path, stamping: bool = False) -> str:
        """kit-health's section 5 alone, on a temp CONTEXT_ROOT — no gh/aws probes, no network (the workspace is
        resolved when the section runs, #91, so the section runs inside the patch)."""
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, {"CONTEXT_ROOT": str(root)}):
            kh = load_kit_health()
            r = kh.Report()
            kh.sec_engine(r, stamping=stamping)
        return "\n".join(r.lines)

    def test_plain_run_never_rewrites_index(self):
        # a plain run executed `make verify index` on the live DB; now it reports the stale index instead
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".context"
            env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_TZ"}
            env["CONTEXT_ROOT"] = str(root)
            subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env=env, check=True, capture_output=True)
            (root / "repos").mkdir()
            (root / "repos" / "a.md").write_text("---\ntitle: A\ntype: repo\ndomain: repos\nstatus: active\nupdated: 2026-09-26\n---\n", encoding="utf-8")
            (root / "INDEX.md").write_text("stale by hand\n", encoding="utf-8")
            report = self.engine_section(root)
            self.assertEqual((root / "INDEX.md").read_text(encoding="utf-8"), "stale by hand\n")  # untouched
            self.assertIn("⚠️ `.context/INDEX.md` is stale", report)
            self.assertIn("a plain run never writes the DB", report)
            self.assertIn("this run re-indexes after the stamp", self.engine_section(root, stamping=True))
            # an oversized doc adds `- <path>: NNKB` notes on stderr; a stale index next to it is still the WARN, not an ERR
            (root / "repos" / "big.md").write_text("---\ntitle: Big\ntype: repo\ndomain: repos\nstatus: active\nupdated: 2026-09-26\n---\n" + "x" * 40_000 + "\n", encoding="utf-8")
            report = self.engine_section(root)
            self.assertIn("⚠️ `.context/INDEX.md` is stale", report)
            self.assertNotIn("❌ `make verify` failed", report)
            # a real schema problem is the ERR
            (root / "repos" / "bad.md").write_text("---\ntitle: Bad\ntype: novel\ndomain: repos\nstatus: active\nupdated: 2026-09-26\n---\n", encoding="utf-8")
            self.assertIn("❌ `make verify` failed", self.engine_section(root))


class CtxStoreCheck(unittest.TestCase):
    """§ 5 reports whether `.context/` is an adopted ctx store, from `ctx_adapter.py adopt --check` (read-only)."""

    def check(self, rc: int, out: str) -> tuple[str, str]:
        kh = load_kit_health()
        calls = []
        def fake_sh(cmd, **kw):
            calls.append(cmd)
            return rc, out, ""
        r = kh.Report()
        with mock.patch.object(kh, "sh", fake_sh):
            kh.ctx_store(r)
        self.assertEqual(calls[0][-2:], ["adopt", "--check"])  # the probe, never the bootstrap itself
        [level] = [k for k, n in r.counts.items() if n]
        return level, r.lines[-1]

    def test_each_answer_maps_to_a_finding_with_its_fix(self):
        self.assertEqual(self.check(0, "store /x: ok: 3 docs checked")[0], "OK")
        level, line = self.check(4, "not adopted: /x is not a ctx store")
        self.assertEqual(level, "WARN")
        self.assertIn("store not adopted", line)
        self.assertIn("ctx_adapter.py adopt`", line)
        self.assertEqual(self.check(1, "")[0], "WARN")
        self.assertIn("ctx_adapter.py install", self.check(1, "")[1])
        level, line = self.check(3, "store /x: …\nfinding: SCHEMA_VIOLATION d/bad status: schema violation")
        self.assertEqual(level, "WARN")
        self.assertIn("1 finding(s)", line)
        self.assertIn("SCHEMA_VIOLATION d/bad", line)
        self.assertEqual(self.check(2, "")[0], "ERR")


class CtxPinCheck(unittest.TestCase):
    """§ 5 also checks the ctx AT THE PIN, not just whether `.context/` is adopted: `where` finds the pinned
    executable (never installs it), then `<ctx> --version` is run directly and compared against the API
    `ctx_adapter.py version`'s second line names — no adopted store is needed for either call."""

    def check(self, **answers) -> tuple[str, str]:
        kh = load_kit_health()

        def fake_sh(cmd, **kw):
            if cmd[-1] == "version":
                return answers.get("adapter_version", (0, "v0.4.0\napi 1", ""))
            if cmd[-1] == "where":
                return answers.get("where", (0, "/opt/ctx", ""))
            if cmd[-1] == "--version":
                return answers.get("ctx_version", (0, "ctx 0.4.0 (api 1)", ""))
            raise AssertionError(cmd)

        r = kh.Report()
        with mock.patch.object(kh, "sh", fake_sh):
            kh.ctx_pin_check(r)
        [level] = [k for k, n in r.counts.items() if n]
        return level, r.lines[-1]

    def test_matching_api_is_ok(self):
        level, line = self.check()
        self.assertEqual(level, "OK")
        self.assertIn("api 1", line)

    def test_pin_missing_is_a_warning_with_the_fix(self):
        level, line = self.check(where=(1, "", "not installed"))
        self.assertEqual(level, "WARN")
        self.assertIn("ctx_adapter.py install && ", line)
        self.assertIn("ctx_adapter.py adopt`", line)

    def test_a_different_api_is_a_warning_naming_both(self):
        with mock.patch.dict(os.environ, {"KIT_CTX": ""}):
            level, line = self.check(ctx_version=(0, "ctx 0.3.0 (api 0)", ""))
        self.assertEqual(level, "WARN")
        self.assertIn("api 0", line)
        self.assertIn("expects api 1", line)
        self.assertIn("remove `", line)  # `install` keeps a usable copy at the pin: removing it is part of the fix
        self.assertIn("ctx_adapter.py adopt`", line)

    def test_a_different_api_under_kit_ctx_names_the_override(self):
        with mock.patch.dict(os.environ, {"KIT_CTX": "/opt/other/ctx"}):
            level, line = self.check(ctx_version=(0, "ctx 9.0.0 (api 2)", ""))
        self.assertEqual(level, "WARN")
        self.assertIn("`KIT_CTX` points at it", line)
        self.assertNotIn("install &&", line)  # install would not clear it: KIT_CTX overrides the pin

    def test_unparseable_ctx_version_output_is_a_warning(self):
        level, line = self.check(ctx_version=(0, "garbage", ""))
        self.assertEqual(level, "WARN")
        self.assertIn("api none", line)


class ConfigSection(unittest.TestCase):
    """kit-health § 3 (config) on a throw-away store — never the live one (kb.ENV / kit_profile.ENV_DIR
    patched directly, the StoreCase pattern test_kb_store.py uses)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kb.ENV, kit_profile.ENV_DIR)
        kb.ENV = self.env
        kit_profile.ENV_DIR = self.env
        kb.init_blank()
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()

    def tearDown(self):
        kb.ENV, kit_profile.ENV_DIR = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()

    def config_report(self) -> str:
        # sec_config reads through `kit_profile.get` (cached, zero-arg) — clear so it sees this test's store,
        # never a prior test's cached one, and clear again after so the next test starts clean too.
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        try:
            kh = load_kit_health()
            r = kh.Report()
            kh.sec_config(r)
            return "\n".join(r.lines)
        finally:
            kit_profile.env_config.cache_clear()
            kit_profile.load.cache_clear()

    def test_disagreeing_deprecated_slack_enabled_warns_loudly(self):
        # one flag per capability: `systems.slack` is the single source pr-open and every other reader gate on now — a store that
        # still carries the retired `slack.enabled` and disagrees with `systems.slack` is exactly the split read
        # this refactor closes; kit-health must say so loudly, not just note the leftover key.
        cfg = kb.load_config()
        cfg["slack"]["enabled"] = True
        cfg["systems"]["slack"] = False
        kb.save_config(cfg)
        report = self.config_report()
        self.assertIn("slack.enabled", report)
        self.assertIn("systems.slack", report)
        self.assertIn("DISAGREE", report)

    def test_agreeing_deprecated_key_still_warns_but_not_loudly(self):
        cfg = kb.load_config()
        cfg["github"]["signed_commits"] = True
        cfg["systems"]["signed_commits"] = True
        kb.save_config(cfg)
        report = self.config_report()
        self.assertIn("github.signed_commits", report)
        self.assertIn("systems.signed_commits", report)
        self.assertNotIn("DISAGREE", report)

    def test_clean_store_has_no_deprecated_key_warning(self):
        self.assertNotIn("deprecated", self.config_report())


class ReviewFindings(unittest.TestCase):
    """the fixed finding shape is counted per level and rule — resolved thread = acted on, open = dismissed."""

    def test_finding_stats_counts_only_bot_findings_in_shape(self):
        kh = load_kit_health()

        def th(body, resolved, login="claude"):
            return {"isResolved": resolved, "comments": {"nodes": [{"author": {"login": login}, "body": body}]}}
        prs = [{"number": 1, "reviewThreads": {"nodes": [
                    th("[STOP] skills/x/SKILL.md:12 — names a workplace (REVIEW.md § 2.1)", True),
                    th("[WARN] setup.sh:40 — is the emptiness meaningful? (REVIEW.md § 2.3)", False),
                    th("[NIT] README.md:3 — typo (REVIEW.md § 3)", True),
                    th("free-form comment without the shape", False),
                    th("[STOP] a.md:1 — from a human, not the bot (x)", False, login="someone")]}},
               {"number": 2, "reviewThreads": {"nodes": [th("**WARN** — old style", False), th("  [WARN] b.sh:2 — no rule given", False)]}}]
        st = kh.finding_stats(prs)
        self.assertEqual((st["prs"], st["findings"], st["acted"], st["dismissed"]), (2, 4, 2, 2))
        self.assertEqual(st["levels"]["STOP"], {"acted": 1, "dismissed": 0})
        self.assertEqual(st["levels"]["WARN"], {"acted": 0, "dismissed": 2})
        self.assertEqual(st["rules"]["REVIEW.md § 2.3"], {"acted": 0, "dismissed": 1})
        self.assertEqual(st["rules"]["unnamed rule"], {"acted": 0, "dismissed": 1})
        self.assertEqual(kh.finding_stats([]), {"prs": 0, "findings": 0, "acted": 0, "dismissed": 0, "levels": {}, "rules": {}})

    def test_review_ratio_is_silent_without_gh_or_remote(self):
        kh = load_kit_health()
        r = kh.Report()
        with mock.patch.object(kh.shutil, "which", return_value=None):
            kh.review_ratio(r)
        self.assertEqual(r.lines, [])


class Seeds(unittest.TestCase):
    """a seeded copy older than its template's last commit is stale; the check never reads git when a time function
    is injected, and a missing file on either side is not stale."""

    def test_stale_seeds_compares_template_commit_time_with_copy_mtime(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as tmp:
            tpl = Path(tmp) / "tpl.md"; copy = Path(tmp) / "copy.md"; missing = Path(tmp) / "none.md"
            tpl.write_text("t\n"); copy.write_text("c\n")
            os.utime(copy, (1_000_000_000, 1_000_000_000))
            pairs = [(tpl, copy), (tpl, missing)]
            self.assertEqual(kh.stale_seeds(pairs, template_time=lambda p: 2_000_000_000), ([(tpl, copy)], []))
            self.assertEqual(kh.stale_seeds(pairs, template_time=lambda p: 0), ([], [(tpl, copy)]))  # no git: not compared, never "fresh"
            self.assertEqual(kh.stale_seeds(pairs, template_time=lambda p: 999_999_999), ([], []))
            self.assertEqual(len(kh.seed_pairs()), 4)
            r = kh.Report()
            with mock.patch.object(kh, "stale_seeds", return_value=([], [(tpl, copy)])):
                kh.seed_wiring(r)
            self.assertTrue(any("not checked" in l for l in r.lines) and not any("not older" in l for l in r.lines))


class PrReviewExampleUnchanged(unittest.TestCase):
    """The kit's own `pr-review/config.example.json` is never the file a user edits — `setup.sh` seeds a copy
    at `.context/state/pr-review/config.json` once and the skill reads that copy — so an edited example is a
    no-op mistake that shows up as a kit diff; kit-health flags it like a leak."""

    def test_the_kits_own_example_still_holds_its_placeholders(self):
        kh = load_kit_health()
        self.assertEqual(kh.pr_review_example_edited(), [])

    def test_a_hand_edited_example_is_flagged_by_key(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.example.json"
            p.write_text(json.dumps({"login": "a-real-login", "owner": "<org>"}), encoding="utf-8")
            self.assertEqual(kh.pr_review_example_edited(p), ["login"])

    def test_missing_or_unparseable_example_is_not_flagged(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "none.json"
            self.assertEqual(kh.pr_review_example_edited(missing), [])
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(kh.pr_review_example_edited(bad), [])


class Verdict(unittest.TestCase):
    """the stamp refuses on un-accepted leak hits, the value scan skips common-word kinds and short plain words,
    every reported value is redacted, the changed-units list comes from git."""

    def test_may_stamp_needs_no_errors_and_no_leak_hits(self):
        kh = load_kit_health()
        r = kh.Report()
        self.assertTrue(kh.may_stamp(r))
        r.leak_hits = 1
        self.assertFalse(kh.may_stamp(r))
        r.leak_hits = 0; r.add(kh.ERR, "x", "boom")
        self.assertFalse(kh.may_stamp(r))

    def test_keep_value_skips_common_kinds_short_words_and_placeholders(self):
        kh = load_kit_health()
        common = {"github.person", "github.team"}
        self.assertFalse(kh.keep_value("Mark", "github.person", common))         # common-word kind, plain single word
        self.assertFalse(kh.keep_value("Marianne", "github.person", common))     # long, still one plain word in a common-word kind
        self.assertTrue(kh.keep_value("First Last", "github.person", common))    # a space: the display name is scanned
        self.assertTrue(kh.keep_value("acme-platform", "github.team", common))   # a hyphen: the team slug is scanned
        self.assertFalse(kh.keep_value("data", "github.label-set", common))      # a plain word under six letters, any kind
        self.assertFalse(kh.keep_value("<owner>", "tracker.repos", common))
        self.assertFalse(kh.keep_value("12345", "aws.account", common))          # short number
        self.assertTrue(kh.keep_value("acme-platform", "slack.channel", common))
        self.assertTrue(kh.keep_value("C0" + "AB12CD3", "slack.channel", common))  # an id from four chars
        self.assertFalse(kh.keep_value("Mark", "slack.user", common))            # a short plain word, kind not flagged
        self.assertTrue(kh.keep_value("acme", ""))                               # a config/identity value keeps the four-char floor
        self.assertTrue(kh.keep_value("Mark", ""))
        self.assertEqual(kh.common_word_kinds() >= {"github.person", "github.team", "github.label-set"}, True)

    def test_fresh_personal_store_does_not_flood_the_scan(self):
        # #118: `setup.sh --personal` lists every `gh repo list` repo; a `config` repo must not make the word a leak,
        # and the user's own login in the kit's `<owner>/<repo>` address is not a colleague login
        kh = load_kit_health()
        cfg = {"github": {"display_names": {"octo-maint": "Octo"}},
               "tracker": {"repos": ["octo-maint/config", "octo-maint/secret-bot"]}}
        with mock.patch.object(kh.kb, "all_facts", return_value={}), \
             mock.patch.object(kh.kit_profile, "load", return_value=cfg), \
             mock.patch.object(kh, "kit_dependencies", return_value=frozenset({"ai-baton"})), \
             mock.patch.object(kh, "identity_env", return_value={}):
            pats, errors = kh.configured_values()
        self.assertEqual(errors, [])

        def hits(line: str) -> list[str]:
            return [what for rx, what in pats if rx.search(line)]
        self.assertEqual(hits("read the pr-review config first"), [])
        self.assertEqual(hits("claude plugin marketplace add octo-maint/ai-baton"), [])
        self.assertIn("tracker.repos", hits("clone octo-maint/config"))              # the full slug still scans
        self.assertIn("tracker.repos (repo name)", hits("the secret-bot runbook"))   # a distinctive name still scans
        self.assertIn("colleague login (github.display_names)", hits("ask octo-maint"))

    def test_leaks_markers_join_the_value_scan(self):
        # #95: `leaks.markers` carries what no generic shape knows (a sandbox product's CLI, a team name)
        kh = load_kit_health()
        cfg = {"leaks": {"markers": ["widgetbox-cli", "ab"]}}
        with mock.patch.object(kh.kb, "all_facts", return_value={}), \
             mock.patch.object(kh.kit_profile, "load", return_value=cfg), \
             mock.patch.object(kh, "identity_env", return_value={}):
            pats, errors = kh.configured_values()
        self.assertEqual(errors, [])
        hits = lambda line: [what for rx, what in pats if rx.search(line)]
        self.assertEqual(hits("run widgetbox-cli first"), ["leaks.markers"])
        self.assertEqual(hits("ab testing"), [])  # a marker under four characters would match everywhere: skipped

    def test_redact_names_the_kind_and_two_characters(self):
        kh = load_kit_health()
        self.assertEqual(kh.redact("Slack channel/DM id", "C0" + "AB12CD3EF"), "Slack channel/DM id:C0…")
        self.assertEqual(kh.redact("env fact `slack.channel`", "team-alerts"), "slack.channel:te…")
        self.assertEqual(kh.redact("tracker.repos (repo name)", "ab"), "tracker.repos:…")
        self.assertNotIn("AB12CD3EF", kh.redact("Slack channel/DM id", "C0" + "AB12CD3EF"))

    def test_codeowners_names_the_kit_owner_without_a_leak(self):
        # #5: the maintainer's `@<owner>` in .github/CODEOWNERS is the kit's own config; another login there, or the
        # owner's handle in any other file, is still a hit
        kh = load_kit_health()
        with mock.patch.object(kh, "identity_env", return_value={"WORKSPACE_GITHUB_LOGIN": "octo-maint"}):
            (rx, _what), = kh.identity_values()

        def hit(frel: str, line: str, owner: str = "octo-maint") -> bool:
            m = rx.search(line)
            return bool(m) and not kh.kit_owner_handle(frel, line, m, owner)
        self.assertFalse(hit(".github/CODEOWNERS", "/.github/workflows/ @octo-maint"))
        self.assertFalse(hit(".github/CODEOWNERS", "/.github/CODEOWNERS @Octo-Maint"))
        self.assertTrue(hit(".github/CODEOWNERS", "/.github/ @octo-maint", owner="someone-else"))  # not the kit's owner
        self.assertTrue(hit(".github/CODEOWNERS", "# maintainer: octo-maint"))                   # not an owner entry
        self.assertTrue(hit("docs/contributing.md", "ask @octo-maint"))                          # not CODEOWNERS
        self.assertTrue(hit(".github/CODEOWNERS", "/.github/ @octo-maint", owner=""))            # owner unknown

    def test_header_time_is_the_users_local_date(self):
        # #12: 01:04 UTC is still the previous evening at UTC-4 — the header names the day the log slug names (a
        # fixed-offset zone: a real zone name in a kit file is a leak shape)
        from zoneinfo import ZoneInfo
        kh = load_kit_health()
        minus4 = kh.dt.timezone(kh.dt.timedelta(hours=-4), "EDT")
        at = kh.dt.datetime(2026, 9, 27, 1, 4, tzinfo=kh.dt.timezone.utc)
        with mock.patch.object(kh.kit_profile, "zone", return_value=(minus4, "")):
            self.assertEqual(kh.header_time(at), "2026-09-26 21:04 EDT")
        with mock.patch.object(kh.kit_profile, "zone", return_value=(ZoneInfo("UTC"), " (UTC — WORKSPACE_TZ 'Mars/Base' unknown)")):
            self.assertEqual(kh.header_time(at), "2026-09-27 01:04 UTC (UTC — WORKSPACE_TZ 'Mars/Base' unknown)")

    def test_changed_units_groups_by_skill_dir(self):
        kh = load_kit_health()
        with mock.patch.object(kh, "sh", return_value=(0, "skills/a/SKILL.md\nskills/a/run.sh\nagents/t.md\nWORKSPACE.md\n", "")):
            self.assertEqual(kh.changed_units("abc"), ["skills/a", "agents/t.md", "WORKSPACE.md"])
        with mock.patch.object(kh, "sh", return_value=(128, "", "unknown revision")):
            self.assertIsNone(kh.changed_units("abc"))  # not "nothing changed": the caller reports it as unknown
        with mock.patch.object(kh, "sh", return_value=(0, "", "")):
            self.assertEqual(kh.changed_units("abc"), [])

    def test_changed_units_remote_reads_the_compare_api(self):
        # #13: a plugin install has no git history — GitHub's compare API names the changed files, grouped the same way
        kh = load_kit_health()
        files = "skills/a/SKILL.md\nskills/a/run.sh\ncontext-db/bin/kb.py\nagents/t.md\nWORKSPACE.md\nREADME.md\n"
        with mock.patch.object(kh.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(kh, "sh", return_value=(0, files, "")) as sh:
            self.assertEqual(kh.changed_units_remote("octo/kit", "aaa", "bbb"), ["skills/a", "agents/t.md", "WORKSPACE.md"])
        self.assertEqual(sh.call_args[0][0][:3], ["gh", "api", "repos/octo/kit/compare/aaa...bbb"])
        with mock.patch.object(kh.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(kh, "sh", return_value=(1, "", "HTTP 404")):
            self.assertIsNone(kh.changed_units_remote("octo/kit", "aaa", "bbb"))  # offline / unknown commit: unknown
        with mock.patch.object(kh.shutil, "which", return_value=None):
            self.assertIsNone(kh.changed_units_remote("octo/kit", "aaa", "bbb"))  # no gh
        self.assertIsNone(kh.changed_units_remote("", "aaa", "bbb"))            # repo unknown

    def test_plugin_stamp_lists_remote_units(self):
        kh = load_kit_health()
        r = kh.Report()
        plugin = {"repo": "octo/kit", "commit": "b" * 40, "version": "1.0.0"}
        with mock.patch.object(kh, "read_health", return_value={"kit_commit": "a" * 40, "last_green": "x"}), \
             mock.patch.object(kh, "sh", return_value=(128, "", "no git")), \
             mock.patch.object(kh, "kit_head", return_value="b" * 40), \
             mock.patch.object(kh.kit_profile, "plugin_install", return_value=plugin), \
             mock.patch.object(kh, "changed_units_remote", return_value=["skills/a"]) as remote:
            kh.sec_stamp(r, "env")
        remote.assert_called_once_with("octo/kit", "a" * 40, "b" * 40)
        self.assertTrue(any("re-reads these): `skills/a`" in ln for ln in r.lines), r.lines)
        r = kh.Report()
        with mock.patch.object(kh, "read_health", return_value={"kit_commit": "b" * 40, "last_green": "x"}), \
             mock.patch.object(kh, "sh", return_value=(128, "", "no git")), \
             mock.patch.object(kh, "kit_head", return_value="b" * 40), \
             mock.patch.object(kh.kit_profile, "plugin_install", return_value=plugin):
            kh.sec_stamp(r, "env")
        self.assertTrue(any("= HEAD" in ln for ln in r.lines) and any("nothing to re-read" in ln for ln in r.lines), r.lines)


class Release(unittest.TestCase):
    """#33: § 1 warns when a newer kit release is out, with the update command for the install mode; a lookup that
    fails is an informational `latest release unknown` line, never a ✅."""
    PLUGIN = {"repo": "owner/kit", "commit": "", "version": "0.2.2", "updated": ""}

    def plugin_run(self, sh_result, gh="/usr/bin/gh"):
        kh = load_kit_health()
        r = kh.Report()
        with mock.patch.object(kh.shutil, "which", return_value=gh), \
             mock.patch.object(kh, "sh", return_value=sh_result) as sh:
            kh.release_check(r, dict(self.PLUGIN), "plugin")
        return kh, r, sh

    def test_plugin_newer_release_warns_with_the_update_command(self):
        out = '{"tagName": "v0.3.0", "publishedAt": "2026-09-27T10:00:00Z", "url": "https://example.invalid/r/v0.3.0"}'
        kh, r, sh = self.plugin_run((0, out, ""))
        self.assertEqual(sh.call_args[0][0][:5], ["gh", "release", "view", "-R", "owner/kit"])
        self.assertEqual(r.counts[kh.WARN], 1, r.lines)
        text = r.findings[0][2]
        for part in ("v0.3.0 available", "installed v0.2.2", "published 2026-09-27", "claude plugin marketplace update ai-baton-kit",
                     "claude plugin update ai-baton@ai-baton-kit", "restart", "https://example.invalid/r/v0.3.0"):
            self.assertIn(part, text)

    def test_plugin_up_to_date_is_ok(self):
        kh, r, _ = self.plugin_run((0, '{"tagName": "v0.2.2", "publishedAt": "", "url": ""}', ""))
        self.assertEqual((r.counts[kh.OK], r.counts[kh.WARN]), (1, 0), r.lines)
        self.assertIn("v0.2.2 = latest release", r.lines[-1])

    def test_plugin_lookup_failure_is_unknown_never_ok(self):
        for result, gh in (((1, "", "HTTP 401: Bad credentials"), "/usr/bin/gh"), ((0, "", ""), None), ((0, "not json", ""), "/usr/bin/gh")):
            kh, r, _ = self.plugin_run(result, gh)
            self.assertEqual((r.counts[kh.OK], r.counts[kh.WARN], r.counts[kh.ERR]), (0, 0, 0), r.lines)
            self.assertIn("latest release unknown", r.lines[-1])
        self.assertIn("`gh` not installed", self.plugin_run((0, "", ""), None)[1].lines[-1])

    def test_plugin_non_version_latest_tag_is_unknown_not_a_crash(self):
        kh, r, _ = self.plugin_run((0, '{"tagName": "v0.3.0-rc1", "publishedAt": "", "url": ""}', ""))
        self.assertEqual((r.counts[kh.OK], r.counts[kh.WARN], r.counts[kh.ERR]), (0, 0, 0), r.lines)
        self.assertIn("cannot compare", r.lines[-1])

    def clone_run(self, remote, describe="v0.2.2", mode="clone"):
        kh = load_kit_health()
        r = kh.Report()

        def fake(cmd, *a, **kw):
            if "ls-remote" in cmd:
                return remote
            if "describe" in cmd:
                return (0, describe, "") if describe else (128, "", "No names found")
            return (0, "", "")
        with mock.patch.object(kh, "sh", side_effect=fake):
            kh.release_check(r, None, mode)
        return kh, r

    def test_clone_newer_tag_on_origin_warns_with_claude_sync(self):
        tags = "a\trefs/tags/v0.2.2\nb\trefs/tags/v0.10.0\nc\trefs/tags/v0.9.1\nd\trefs/tags/vnext\n"
        kh, r = self.clone_run((0, tags, ""))
        self.assertEqual(r.counts[kh.WARN], 1, r.lines)
        self.assertIn("v0.10.0 available (installed v0.2.2)", r.findings[0][2])  # numeric, not lexical, order
        self.assertIn("make claude_sync", r.findings[0][2])

    def test_clone_up_to_date_and_unknown(self):
        kh, r = self.clone_run((0, "a\trefs/tags/v0.2.2\n", ""))
        self.assertEqual((r.counts[kh.OK], r.counts[kh.WARN]), (1, 0), r.lines)
        kh, r = self.clone_run((128, "", "fatal: unable to access origin"))
        self.assertEqual(r.counts[kh.OK] + r.counts[kh.WARN], 0, r.lines)
        self.assertIn("latest release unknown", r.lines[-1])
        kh, r = self.clone_run((0, "a\trefs/tags/v0.2.2\n", ""), describe="")
        self.assertEqual(r.counts[kh.OK] + r.counts[kh.WARN], 0, r.lines)
        self.assertIn("cannot compare", r.lines[-1])

    def test_dev_checkout_updates_with_git_pull_not_claude_sync(self):
        # #34: the update command comes from the mode table — a dev checkout follows its own branch
        kh, r = self.clone_run((0, "a\trefs/tags/v0.3.0\n", ""), mode="dev-checkout")
        self.assertEqual(r.counts[kh.WARN], 1, r.lines)
        text = r.findings[0][2]
        self.assertIn(f"git -C {kh.KIT} pull --ff-only", text)
        self.assertNotIn("run `make claude_sync`", text)


class LazyWorkspace(unittest.TestCase):
    """#91: kit-health resolves the workspace when a section runs, not at import, so a test that points `KIT` at a
    fixture never reads the developer's live `.context/` (or its `settings.local.json`) beside the real kit."""

    def test_a_fixture_kit_never_reaches_the_live_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live" / ".context"          # stands in for the populated workspace beside the real kit
            (live / "reference" / "env").mkdir(parents=True)
            (live / "reference" / "env" / "config.json").write_text('{"environment": "live-marker-91"}')
            fixture = Path(tmp) / "fixture" / "kit"
            fixture.mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if k != "CONTEXT_ROOT"}
            with mock.patch.dict(os.environ, env, clear=True):
                kh = load_kit_health()                          # import resolves nothing any more
                with mock.patch.object(kh.kit_profile, "context_root", return_value=live):
                    self.assertEqual(kh.ctx(), live)            # the shipped kit: context_root() as before
                    kh.KIT = fixture
                    self.assertEqual(kh.ctx(), fixture.parent / ".context")
                    self.assertEqual(kh.root(), fixture.parent)
                    self.assertNotIn("live", str(kh.settings_local_path()))
                    kh.CTX = live                               # an explicit override still wins
                    self.assertEqual(kh.ctx(), live)
class SandboxMarkers(unittest.TestCase):
    """#94: kit-health's sandbox probe reads `kit.sandbox_markers` from the env store; the kit names no sandbox product of its own."""

    def test_markers_are_paths_or_env_vars_and_none_means_no_check(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"KIT_TEST_SANDBOX": "1"}):
            os.environ.pop("KIT_TEST_UNSET", None)
            self.assertFalse(kh.sandbox_detected([]))
            self.assertTrue(kh.sandbox_detected([tmp]))                       # an absolute path that exists
            self.assertFalse(kh.sandbox_detected([tmp + "/missing"]))
            self.assertTrue(kh.sandbox_detected(["KIT_TEST_UNSET", "KIT_TEST_SANDBOX"]))  # any set variable
            self.assertFalse(kh.sandbox_detected(["KIT_TEST_UNSET", ""]))


class NoTraceback(unittest.TestCase):
    """kit-health degrades to a RED finding, always leaves its report, and exits with the verdict."""

    def blank(self, tmp: str) -> tuple[Path, dict]:
        root = Path(tmp) / ".context"
        env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_")}
        env["CONTEXT_ROOT"] = str(root)
        subprocess.run([sys.executable, str(KIT / "context-db" / "bin" / "kb.py"), "init", "--blank"], env=env, check=True, capture_output=True)
        return root, env

    def test_a_null_config_section_is_no_crash(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root, env = self.blank(tmp)
            cfg_path = next(root.rglob("config.json"))
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.update(github=None, slack=None, tracker=None)
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            report = Path(tmp) / "out" / "report.md"
            p = subprocess.run([sys.executable, str(KIT / "skills" / "kit-health" / "kit-health.py"), "--ci", "--report", str(report)],
                               env=env, capture_output=True, text=True, cwd=KIT)
            self.assertNotIn("Traceback", p.stderr)
            self.assertIn(p.returncode, (0, 2), p.stdout + p.stderr)
            self.assertTrue(report.is_file())

    def test_a_crashing_section_is_a_red_finding_and_the_report_is_written(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "r.md"

            def boom(_r):
                raise TypeError("'NoneType' object is not iterable")
            argv = ["kit-health.py", "--ci", "--quiet", "--report", str(report)]
            with mock.patch.object(kh, "sec_config", boom), mock.patch.object(sys, "argv", argv), \
                    mock.patch("builtins.print"):
                rc = kh.main()
            self.assertEqual(rc, 2)
            text = report.read_text(encoding="utf-8")
            self.assertIn("section crashed: `TypeError", text)
            self.assertIn("**RED** (CI)", text)

    def test_a_crash_outside_the_sections_still_writes_the_report(self):
        kh = load_kit_health()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "r.md"
            argv = ["kit-health.py", "--ci", "--report", str(report)]
            with mock.patch.object(kh, "header_time", side_effect=RuntimeError("clock")), mock.patch.object(sys, "argv", argv):
                with self.assertRaises(RuntimeError):
                    kh.main()
            self.assertTrue(report.is_file())

    def test_every_flag_has_help(self):
        p = subprocess.run([sys.executable, str(KIT / "skills" / "kit-health" / "kit-health.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--stale DAYS", p.stdout)
        self.assertIn("metadata.reviewed", p.stdout)
        self.assertIn("exit 0 GREEN", p.stdout)

class CtxDependency(unittest.TestCase):
    """The ctx adapter's pinned upstream is a dependency the kit calls, not an environment value: the
    `<org>/<repo>` path shape must skip it, as it skips workflow `uses:` repos."""

    def test_the_pinned_ctx_repo_is_a_kit_dependency(self):
        kh = load_kit_health()
        kh.kit_dependencies.cache_clear()
        m = re.search(r'^CTX_REPO\s*=\s*"[^"]*/([\w.-]+)"', (kh.KIT / "context-db" / "bin" / "ctx_adapter.py")
                      .read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(m, "ctx_adapter.py names its upstream as CTX_REPO")
        self.assertIn(m.group(1), kh.kit_dependencies())

    def test_the_org_path_shape_skips_it_and_still_catches_other_repos(self):
        kh = load_kit_health()
        org = "o" + "rgx" * 2
        cfg = {"github": {"org": org}}
        with mock.patch.object(kh.kb, "all_facts", return_value={}), \
             mock.patch.object(kh.kit_profile, "load", return_value=cfg), \
             mock.patch.object(kh, "kit_dependencies", return_value=frozenset({"ctx-store"})), \
             mock.patch.object(kh, "identity_env", return_value={}):
            pats, _ = kh.configured_values()
        def hits(line: str) -> list[str]:
            return [what for rx, what in pats if rx.search(line) and "<repo>" in what]
        self.assertEqual(hits(f'CTX_REPO = "https://github.com/{org}/ctx-store"'), [])
        self.assertTrue(hits(f"see https://github.com/{org}/private-thing"))


if __name__ == "__main__":
    unittest.main()
