"""kit_verify --no-env — the environment-free validator: passes on a bare clone with no .context/, checks a
fixture skill's body, fails the leaky fixture. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(HERE.parent))

import kit_verify  # noqa: E402
from tests import hermetic_env  # noqa: E402

FIX = HERE / "fixtures" / "skills"
LEAK = "C0" + "AB12CD3EF"  # the leaky fixture's Slack-shaped id, assembled so no scanner reads this file as a leak
FIELD = "customfield_" + "10020"  # a custom-field-shaped id, likewise


def run(*args: str, env_root: str = "/nonexistent/.context") -> tuple[int, str, str]:
    """kit_verify.py in a subprocess with CONTEXT_ROOT pointing nowhere — the bare-clone situation."""
    env = dict(os.environ, CONTEXT_ROOT=env_root)
    p = subprocess.run([sys.executable, str(BIN / "kit_verify.py"), *args], capture_output=True, text=True, env=env, cwd=KIT)
    return p.returncode, p.stdout, p.stderr


class NoEnv(unittest.TestCase):
    def test_bare_clone_passes_and_names_the_skipped_checks(self):
        rc, out, err = run("--no-env")
        self.assertEqual(rc, 0, err)
        self.assertIn("env store not checked (--no-env)", out)
        self.assertIn("~ skipped: env store", err)

    def test_without_no_env_a_missing_store_is_a_failure(self):
        rc, _, err = run()
        self.assertEqual(rc, 1)
        self.assertIn("no configuration: env store", err)

    def test_good_fixture_passes_with_partial_skips_named(self):
        rc, out, err = run("--no-env", str(FIX / "good-skill"))
        self.assertEqual(rc, 0, err)
        self.assertIn("1 skills/agents verified", out)
        self.assertIn("~ skipped: always-on budget", err)

    def test_leaky_fixture_fails_on_leak_missing_script_and_legacy_key(self):
        rc, _, err = run("--no-env", str(FIX / "leaky-skill" / "SKILL.md"))
        self.assertEqual(rc, 1)
        self.assertIn(f"Slack channel/DM id `{LEAK}`", err)
        self.assertIn("cites `scripts/missing.sh` but", err)
        self.assertIn("'version:' belongs under `metadata:`", err)

    def test_real_number_behind_a_placeholder_prefix_fails(self):
        # #92: written at run time, so no file in the kit carries the real-looking number
        import shutil, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "placeholder-skill"
            shutil.copytree(FIX / "good-skill", skill)
            md = skill / "SKILL.md"
            md.write_text(md.read_text() + "\nExample commit: KEY-" + "9876 fix the thing.\n")
            rc, _, err = run("--no-env", str(md))
        self.assertEqual(rc, 1, err)
        self.assertIn("real-looking number behind a placeholder prefix", err)

    def test_readme_install_has_no_python_one_liner(self):
        # #97: the README's plugin install uses /kit-setup, not a `claude plugin list --json | python3 -c` lookup
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            (kit / "README.md").write_text("```sh\nsh \"$(claude plugin list --json | python3 -c 'x')/setup.sh\"\n```\n")
            errors: list[str] = []
            kit_verify.check_readme_install(errors, kit)
            self.assertTrue(errors and "README.md:2" in errors[0] and "/kit-setup" in errors[0], errors)
            (kit / "README.md").write_text("```sh\ncd ~/Projects && claude\n/kit-setup\n```\n")
            errors = []
            kit_verify.check_readme_install(errors, kit)
            self.assertEqual(errors, [])
        errors = []
        kit_verify.check_readme_install(errors)  # the kit's own README
        self.assertEqual(errors, [])

    def test_unknown_path_is_a_usage_error(self):
        rc, _, err = run("--no-env", str(FIX / "no-such-skill"))
        self.assertEqual(rc, 2)
        self.assertIn("no such file", err)


class NoEnvOverlay(unittest.TestCase):
    """--no-env must never consult a real store's `_discovery/` overlay — a bare clone has none, but a
    contributor's own machine (or CI running the same checkout) does, and its business is kit-health's, not
    the environment-free validator's."""

    def store(self, tmp: str) -> Path:
        root = Path(tmp) / ".context"
        env = dict(os.environ, CONTEXT_ROOT=str(root))
        subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env=env, check=True, capture_output=True)
        return root

    def test_a_valid_overlay_does_not_change_the_discoverable_facts_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.store(tmp)
            baseline_rc, baseline_out, baseline_err = run("--no-env", env_root=str(root))
            self.assertEqual(baseline_rc, 0, baseline_err)
            discovery = root / "reference" / "env" / "_discovery"
            discovery.mkdir(parents=True, exist_ok=True)
            (discovery / "zzz-extra.json").write_text(json.dumps({
                "system": "widget",
                "facts": [{"key": "widget.thing", "target": "row", "tool": "cli", "args": {"cmd": "true"},
                           "verify": "one clause", "ttl_days": 0}],
            }), encoding="utf-8")
            rc, out, err = run("--no-env", env_root=str(root))
            self.assertEqual(rc, 0, err)
            self.assertEqual(out, baseline_out)  # the overlay's extra fact must not be counted

    def test_a_poisoned_overlay_does_not_crash_or_change_the_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.store(tmp)
            baseline_rc, baseline_out, baseline_err = run("--no-env", env_root=str(root))
            discovery = root / "reference" / "env" / "_discovery"
            discovery.mkdir(parents=True, exist_ok=True)
            # a dangling symlink: `_discovery/*.json` globs it by name, but reading it raises OSError, not
            # json.JSONDecodeError — under --no-env it must never even be opened
            (discovery / "poison.json").symlink_to(discovery / "does-not-exist.json")
            rc, out, err = run("--no-env", env_root=str(root))
            self.assertEqual(rc, baseline_rc, err)
            self.assertEqual(out, baseline_out)
            self.assertNotIn("Traceback", err)


class LoadJsonFile(unittest.TestCase):
    """`load_json_file()` is the one place every JSON-reading check in kit_verify.py goes through — bad JSON,
    a non-UTF-8 byte, or an unreadable/dangling path must all come back as (None, reason), never a traceback."""

    def test_valid_json_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ok.json"
            p.write_text('{"a": 1}', encoding="utf-8")
            data, err = kit_verify.load_json_file(p)
            self.assertEqual(data, {"a": 1})
            self.assertIsNone(err)

    def test_invalid_json_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            data, err = kit_verify.load_json_file(p)
            self.assertIsNone(data)
            self.assertIsNotNone(err)

    def test_non_utf8_bytes_are_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad-encoding.json"
            p.write_bytes(b"\xff\xfe{not utf-8")
            data, err = kit_verify.load_json_file(p)
            self.assertIsNone(data)
            self.assertIsNotNone(err)

    def test_a_dangling_symlink_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dangling.json"
            p.symlink_to(Path(tmp) / "does-not-exist.json")
            data, err = kit_verify.load_json_file(p)
            self.assertIsNone(data)
            self.assertIsNotNone(err)

    def test_unreadable_and_invalid_json_are_worded_differently(self):
        # a deferred review nit: every caller used to label both kinds "invalid JSON — …", so an unreadable or
        # dangling file read as a parse error it never was
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            _, err = kit_verify.load_json_file(bad)
            self.assertTrue(err.startswith("invalid JSON — "), err)
            dangling = Path(tmp) / "dangling.json"
            dangling.symlink_to(Path(tmp) / "does-not-exist.json")
            _, err2 = kit_verify.load_json_file(dangling)
            self.assertTrue(err2.startswith("unreadable — "), err2)


class PluginManifest(unittest.TestCase):
    """plugin.json version == VERSION, kebab name, the marketplace entry points at the root — env-free."""

    def check(self, kit: Path) -> list[str]:
        saved = kit_verify.KIT
        kit_verify.KIT = kit
        try:
            errors: list[str] = []
            kit_verify.check_plugin_manifest(errors)
            return errors
        finally:
            kit_verify.KIT = saved

    def write(self, kit: Path, version="1.2.3", name="my-kit", market_name=None,
              market_source=None, kit_version="1.2.3"):
        if market_source is None:
            market_source = {"source": "github", "repo": "o/my-kit", "ref": f"v{kit_version}"}
        (kit / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        # the identity options and the hook are what the real kit ships; check_identity_options has its own tests
        (kit / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": version, "userConfig": {
            k: {"type": "string", "title": "t", "description": "d"} for k in kit_verify.kit_profile.IDENTITY_KEYS.values()}}), encoding="utf-8")
        (kit / "hooks").mkdir(exist_ok=True)
        (kit / "hooks" / "hooks.json").write_bytes((KIT / "hooks" / "hooks.json").read_bytes())
        (kit / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"name": "m", "owner": {"name": "o"}, "plugins": [{"name": market_name or name, "source": market_source}]}), encoding="utf-8")
        (kit / "VERSION").write_text(kit_version + "\n", encoding="utf-8")
        (kit / ".git").mkdir(exist_ok=True)  # the kit's own checkout: the marketplace pin is enforced there

    def test_the_kit_agrees(self):
        self.assertEqual(self.check(KIT), [])

    def test_version_drift_bad_name_and_marketplace_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            self.write(kit)
            self.assertEqual(self.check(kit), [])
            self.write(kit, version="1.2.4")
            self.assertTrue(any("version '1.2.4' != VERSION '1.2.3'" in e for e in self.check(kit)))
            self.write(kit, name="My Kit")
            self.assertTrue(any("not kebab-case" in e for e in self.check(kit)))
            self.write(kit, market_name="other")
            self.assertTrue(any("no plugins[] entry" in e for e in self.check(kit)))
            self.write(kit, market_source="./plugins/x")
            self.assertTrue(any("is not a github source" in e for e in self.check(kit)))
            self.write(kit, market_source={"source": "github", "repo": "o/my-kit", "ref": "v9.9.9"})
            self.assertTrue(any("source.ref 'v9.9.9' != 'v1.2.3'" in e for e in self.check(kit)))
            self.write(kit, market_source="./")
            (kit / ".git").rmdir()  # an installed copy (the plugin cache, the install smoke's `./` swap): not a checkout
            self.assertEqual(self.check(kit), [])
            (kit / ".claude-plugin" / "plugin.json").write_text("{nope", encoding="utf-8")
            self.assertTrue(any("invalid JSON" in e for e in self.check(kit)))
            (kit / ".claude-plugin" / "plugin.json").unlink()
            self.assertTrue(any("plugin.json: missing" in e for e in self.check(kit)))


class ProjectDirContextRoot(unittest.TestCase):
    def test_plugin_path_finds_the_projects_context_dir(self):
        # with the kit installed elsewhere, CLAUDE_PROJECT_DIR/.context is the store's home
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / ".context").mkdir(parents=True)
            # a kit copy deep in a "plugin cache" with no sibling .context/, so only the fallback can answer
            cache_bin = Path(tmp) / "cache" / "plugins" / "kit" / "context-db" / "bin"
            cache_bin.mkdir(parents=True)
            (cache_bin / "kit_profile.py").write_bytes((BIN / "kit_profile.py").read_bytes())
            code = "import kit_profile; print(kit_profile.context_root())"
            env = {k: v for k, v in os.environ.items() if k != "CONTEXT_ROOT"}
            r = subprocess.run([sys.executable, "-c", code], cwd=cache_bin, env={**env, "CLAUDE_PROJECT_DIR": str(proj)},
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), str(proj / ".context"))  # the project dir's, not the cache's
            r = subprocess.run([sys.executable, "-c", code], cwd=cache_bin, env={**env, "CLAUDE_PROJECT_DIR": str(proj), "CONTEXT_ROOT": str(proj / "x")},
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), str(proj / "x"))  # CONTEXT_ROOT still wins
            r = subprocess.run([sys.executable, "-c", code], cwd=cache_bin, env=env, capture_output=True, text=True)
            # kit_profile resolves __file__ (symlinks, and macOS's /var -> /private/var) before deriving KIT.parent
            self.assertEqual(Path(r.stdout.strip()).resolve(), (cache_bin.parents[2] / ".context").resolve())  # nothing set: beside the kit, as before


class BodyChecks(unittest.TestCase):
    def check(self, body: str, unit_dir: Path | None = None, is_agent: bool = False) -> list[str]:
        errors: list[str] = []
        p = (unit_dir or FIX / "good-skill") / "SKILL.md"
        kit_verify.check_body(p, p.relative_to(KIT), body, errors, is_agent=is_agent)
        return errors

    def check_warn(self, body: str, unit_dir: Path | None = None) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warn: list[str] = []
        p = (unit_dir or FIX / "good-skill") / "SKILL.md"
        kit_verify.check_body(p, p.relative_to(KIT), body, errors, warn=warn)
        return errors, warn

    def test_agent_body_needs_colon_fails_but_skill_body_is_untouched(self):
        # the fork hand-back line is `NEEDS <system>.<kind> <name>`, never a colon — a main session
        # matching on one spelling misses the other. kit-verify only enforces this on an agent body.
        line = "NEEDS: nothing | <one line>"
        errors = self.check(line, is_agent=True)
        self.assertTrue(any("NEEDS:" in e and "colon" in e for e in errors), errors)
        self.assertEqual(self.check(line, is_agent=False), [])  # a skill body is not a fork hand-back contract
        self.assertEqual(self.check("NEEDS nothing | <one line>", is_agent=True), [])  # the one spelling passes
        self.assertEqual(self.check("NEEDS <system>.<kind> <name>", is_agent=True), [])

    def test_own_paths_globs_and_placeholders(self):
        self.assertEqual(self.check("run `scripts/hello.sh` and `scripts/*.sh` and `scripts/<x>.sh`"), [])
        errors = self.check("run `scripts/nope.sh`")
        self.assertTrue(any("cites `scripts/nope.sh`" in e for e in errors), errors)
        self.assertEqual(self.check("see `$BATON/skills/other-skill/scripts/x.sh`"), [])  # another unit's file
        errors = self.check("run `$BATON/skills/good-skill/scripts/nope.sh`")  # the kit-root form is checked like a relative path
        self.assertTrue(any("cites `.claude/skills/good-skill/scripts/nope.sh`" in e for e in errors), errors)
        errors = self.check("see `.claude/skills/other-skill/scripts/x.sh`")  # the clone spelling: no such path on a plugin (#3)
        self.assertTrue(any("write `$BATON/…`" in e for e in errors), errors)

    def test_cross_skill_path_literal_is_rejected(self):
        # cross-reference a skill by name; a `skills/<x>/SKILL.md` literal breaks on every host with another layout
        errors = self.check("read `$BATON/skills/other-skill/SKILL.md` § Rules")
        self.assertTrue(any("cross-reference a skill by name (`other-skill`" in e for e in errors), errors)
        errors = self.check("see `skills/other-skill/` for the rest")
        self.assertTrue(any("cross-reference a skill by name" in e for e in errors), errors)
        self.assertEqual(self.check("run `$BATON/skills/other-skill/scripts/x.sh` first"), [])  # a script a step runs: allowed
        self.assertEqual(self.check("this is `$BATON/skills/good-skill/SKILL.md`"), [])  # its own file
        self.assertEqual(self.check("see `~/my-skills/other/` or https://x.invalid/skills/other/"), [])  # not a kit path: left boundary
        for installed in ("`~/.claude/skills/other-skill/SKILL.md`", "`$HOME/.claude/skills/other-skill/`"):  # the installed-path forms
            self.assertTrue(any("cross-reference a skill by name" in e for e in self.check(f"read {installed}")), installed)
        self.assertEqual(self.check("the `other-skill` skill, or `/other-skill`"), [])  # by name

    def test_body_length_cap(self):
        errors = self.check("x\n" * (kit_verify.BODY_MAX_LINES + 1))
        self.assertTrue(any(f"lines > {kit_verify.BODY_MAX_LINES}" in e for e in errors), errors)
        self.assertEqual(self.check("x\n" * kit_verify.BODY_MAX_LINES), [])

    def test_body_length_cap_is_300_with_a_130_line_warn(self):
        # a follow-up on the size-and-references work: the hard cap moved down from evolve's old 500-line Tier 0
        # cap to 300, with a new warn from 130 (docs/authoring.md's target) for a unit not named in
        # BODY_LINES_ALLOW. Pinned by the literal numbers, not just the symbols, so a change to either constant's
        # value is caught here even if it forgets to update the docs that quote it.
        self.assertEqual(kit_verify.BODY_MAX_LINES, 300)
        self.assertEqual(kit_verify.BODY_WARN_LINES, 130)
        errors = self.check("x\n" * 301)
        self.assertTrue(any("lines > 300" in e for e in errors), errors)
        self.assertEqual(self.check("x\n" * 300), [])
        errors, warn = self.check_warn("x\n" * 131)
        self.assertEqual(errors, [])
        self.assertTrue(any("lines > 130" in w for w in warn), warn)
        errors, warn = self.check_warn("x\n" * 130)
        self.assertEqual(warn, [])

    def test_body_lines_allow_skips_the_warning_but_never_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, over in (("pr-review", True), ("self-assessment", True), ("some-other-skill", False)):
                unit_dir = Path(tmp) / name
                unit_dir.mkdir()
                p = unit_dir / "SKILL.md"
                p.write_text("body\n", encoding="utf-8")  # leak_shapes reads this file directly; irrelevant here
                errors: list[str] = []
                warn: list[str] = []
                # between the two thresholds: an allow-listed unit is silent, any other unit warns
                kit_verify.check_body(p, p, "x\n" * (kit_verify.BODY_WARN_LINES + 20), errors, warn=warn)
                self.assertEqual(errors, [], name)
                if over:
                    self.assertEqual(warn, [], name)
                else:
                    self.assertTrue(any(f"lines > {kit_verify.BODY_WARN_LINES}" in w for w in warn), (name, warn))
                # past the cap: every unit errors, allow-listed or not
                errors, warn = [], []
                kit_verify.check_body(p, p, "x\n" * (kit_verify.BODY_MAX_LINES + 50), errors, warn=warn)
                self.assertTrue(any(f"lines > {kit_verify.BODY_MAX_LINES}" in e for e in errors), (name, errors))

    def test_shape_scan_uses_the_shared_list_and_skip_rule(self):
        import leak_shapes
        text = f"channel {LEAK}\n  facts: \"slack.channel {LEAK}\"\n{FIELD}\nsee leak_shapes.py for {LEAK}\n"
        hits = leak_shapes.scan(text)
        self.assertEqual([(n, what) for n, what, _ in hits], [(1, "Slack channel/DM id"), (3, "tracker custom-field id"), (4, "Slack channel/DM id")])
        self.assertIn("leak_shapes.py", leak_shapes.SKIP_FILES)  # the scanners' own files are skipped per file, not per line

    def test_allow_list_is_honoured_and_a_bad_regex_is_skipped(self):
        import io
        import contextlib
        import leak_shapes
        with tempfile.TemporaryDirectory() as tmp:
            allow = Path(tmp) / "allow.txt"
            allow.write_text(f"# header\nskills/demo/SKILL.md:{LEAK}   # a dated example\n[unclosed\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                pats = leak_shapes.allowed(allow)
            self.assertEqual(len(pats), 1)
            self.assertIn("allow-list regex does not compile", err.getvalue())
            text = f"post to {LEAK}\n"
            self.assertEqual(leak_shapes.scan(text, rel="skills/demo/SKILL.md", allow=pats), [])
            self.assertEqual(len(leak_shapes.scan(text, rel="skills/other/SKILL.md", allow=pats)), 1)
            self.assertEqual(leak_shapes.allowed(Path(tmp) / "missing.txt"), ())
            self.assertIs(leak_shapes.allowed(allow), pats)  # compiled once per process and file


class CtxWriteProse(unittest.TestCase):
    """`.context/` writes go through the ctx MCP tools, never Write/Edit/a shell redirect (#322/#324) — the
    PreToolUse deny catches a Write/Edit tool call; this prose check exists for what it cannot see: a
    Bash-run heredoc/`sed -i`/`tee`/`cat >`/`>>`, or wording that never names which tool routes the change."""

    def hits(self, body: str) -> list[str]:
        return [msg for _line, msg in kit_verify.ctx_write_prose_hits(body)]

    def test_append_a_line_elsewhere_is_not_a_hit(self):
        self.assertEqual(self.hits("1. Append a line to the PR body with the test result.\n"), [])
        self.assertEqual(self.hits("1. Append a line to `.context/sessions/x.md`.\n"), [])  # an exempt path
        self.assertTrue(self.hits("1. Append a line to the context doc's Session log.\n"))

    def test_exempt_mirrors_the_deny_hooks_skip_list(self):
        exempt = kit_verify._ctx_write_exempt
        for rel in ("sessions/foo.md", "state/bar.md", "handoff/x.md", "_templates/y.md", "bin/z.md",
                    ".hidden/x.md", "reference/INDEX.md", "reference/SESSION_INDEX.md", "reference/README.md",
                    "reference/x.jsonl", "reference/x.txt"):
            self.assertTrue(exempt(rel), rel)
        for rel in ("reference/priorities.md", "reference/claude-cost-tracking.md", "pr-reviews/repo.md",
                    "on-call/rotations/2026-W40.md"):
            self.assertFalse(exempt(rel), rel)

    def test_edit_a_backticked_path_is_a_hit(self):
        hits = self.hits("Edit `.context/reference/priorities.md` and tick the box.")
        self.assertTrue(any("reference/priorities.md" in h and "without naming a ctx tool" in h for h in hits), hits)

    def test_write_to_is_a_hit(self):
        hits = self.hits("Write the outcome to `.context/reference/notes.md` when you're done.")
        self.assertTrue(any("reference/notes.md" in h for h in hits), hits)

    def test_shell_redirects_into_a_context_doc_are_hits(self):
        for line in (
            "`sed -i 's/a/b/' .context/reference/notes.md`",
            "pipe the summary with `cat >> .context/reference/notes.md`",
            "`tee .context/reference/notes.md` the block",
            "append with a heredoc: `cat > .context/reference/notes.md <<'EOF'`",
        ):
            hits = self.hits(line)
            self.assertTrue(any("reference/notes.md" in h for h in hits), (line, hits))

    def test_reading_a_doc_is_not_a_hit(self):
        self.assertEqual(self.hits("Read `.context/reference/priorities.md` before you start."), [])

    def test_a_ctx_tool_named_in_the_same_unit_exempts_it(self):
        self.assertEqual(self.hits("Edit `.context/reference/priorities.md` through `ctx_str_replace`."), [])
        self.assertEqual(self.hits("Edit `.context/reference/priorities.md` via `ctx_adapter.py ctx str-replace`."), [])
        self.assertEqual(self.hits("Tick `.context/reference/priorities.md` — through the ctx tools, see step 1."), [])

    def test_exempt_skip_dir_write_is_not_a_hit_even_with_a_write_verb(self):
        self.assertEqual(self.hits("Append a row to `.context/sessions/_ledger.md` by hand."), [])

    def test_a_write_verb_far_from_an_unrelated_path_in_the_same_step_is_not_a_hit(self):
        # the verb targets the exempt sessions/ path; a second, non-exempt path mentioned only in passing
        # (a read-only pipeline note, well outside the proximity window) must not be flagged for it
        pad = "x" * 200
        body = f"One row is appended to `.context/sessions/_ledger.md`. {pad} It feeds `.context/reference/report.md`."
        self.assertEqual(self.hits(body), [])

    def test_append_a_line_without_a_path_or_ctx_tool_is_a_hit(self):
        hits = self.hits("Then append a line to the context doc with the outcome.")
        self.assertTrue(any("append a line" in h for h in hits), hits)

    def test_append_a_line_naming_a_ctx_tool_is_not_a_hit(self):
        self.assertEqual(self.hits("Then append a line to the context doc via `ctx_log`."), [])

    def test_wired_into_check_body(self):
        errors: list[str] = []
        p = FIX / "good-skill" / "SKILL.md"
        kit_verify.check_body(p, p.relative_to(KIT), "Edit `.context/reference/priorities.md` directly.", errors)
        self.assertTrue(any("without naming a ctx tool" in e for e in errors), errors)


class IssueRefs(unittest.TestCase):
    """A kit file may not cite `#n` past this tracker's reach: the release CHANGELOG's highest issue + the margin."""

    def kit(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "CHANGELOG.md").write_text("* fix ([#40](https://github.com/o/r/issues/40)) ([abc](https://github.com/o/r/commit/abc))\n")
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        return root

    def check(self, root: Path) -> list[str]:
        errors: list[str] = []
        kit_verify.check_issue_refs(errors, root)
        return errors

    def test_ceiling_from_changelog(self):
        self.assertEqual(kit_verify.issue_ref_ceiling(self.kit({})), 40 + kit_verify.ISSUE_REF_MARGIN)

    def test_ref_past_the_ceiling_fails(self):
        errors = self.check(self.kit({"bin/x.py": "ok = 1  # (#12)\nbad = 2  # (#116)\n"}))
        self.assertEqual(len(errors), 1)
        self.assertIn("bin/x.py:2: cites #116", errors[0])

    def test_examples_history_and_fixtures_are_exempt(self):
        self.assertEqual(self.check(self.kit({
            "skills/a/SKILL.md": "A GitHub key looks like `#162`; a hex colour #000000 is no ref.\n",
            "context-db/tests/test_y.py": 'FIXTURE = "Closes #404"\n',
            "evals/e/prompt.md": "PR #310 is up.\n",
        })), [])

    def test_no_changelog_skips(self):
        root = self.kit({"x.md": "#999\n"})
        (root / "CHANGELOG.md").unlink()
        self.assertEqual(self.check(root), [])


class DescriptionShape(unittest.TestCase):
    """A description a reader cannot tell the trigger from, or one written in the first person, is a
    warning (not a failure yet — 15 of 18 units predate the rule; the PR body lists them)."""

    def test_no_trigger_phrase_warns(self):
        warn: list[str] = []
        kit_verify.check_description_shape("skills/x/SKILL.md", {"description": "Formats a report nicely."}, warn)
        self.assertTrue(any("no trigger" in w for w in warn), warn)

    def test_a_trigger_phrase_is_silent(self):
        warn: list[str] = []
        kit_verify.check_description_shape("skills/x/SKILL.md", {"description": "Formats a report. Use when asked to format one."}, warn)
        self.assertEqual(warn, [])

    def test_first_person_is_flagged(self):
        warn: list[str] = []
        kit_verify.check_description_shape("skills/x/SKILL.md", {"description": "I format a report when asked."}, warn)
        self.assertTrue(any("first person" in w for w in warn), warn)

    def test_a_when_only_in_the_not_for_clause_still_warns(self):
        # a near miss can itself be conditional ("Not for … when …") — that "when" names what the unit does
        # NOT do, not when it fires, so it must not silence the no-trigger warning.
        warn: list[str] = []
        kit_verify.check_description_shape(
            "skills/x/SKILL.md", {"description": "Formats a report. Not for a draft, even when it looks final."}, warn)
        self.assertTrue(any("no trigger" in w for w in warn), warn)


class ReviewedFreshness(unittest.TestCase):
    """`reviewed:` older than the file's last committed edit is a warning; --no-git and an unknown path
    (no git repository, or a file git has never seen) are both silently skipped, never guessed at."""

    def repo(self, tmp: str, rel: str = "skill.md", content: str = "x") -> Path:
        kit = Path(tmp)
        git_env = hermetic_env(kit)  # every git call below ignores the host's own commit.gpgsign / gpg.format
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=kit, check=True, capture_output=True, env=git_env)
        p = kit / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", rel], cwd=kit, check=True, capture_output=True, env=git_env)
        commit_env = {**git_env, "GIT_AUTHOR_DATE": "2026-09-20T12:00:00", "GIT_COMMITTER_DATE": "2026-09-20T12:00:00"}
        subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=kit, check=True, capture_output=True, env=commit_env)
        return kit

    def test_reviewed_before_the_last_commit_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = self.repo(tmp)
            warn: list[str] = []
            kit_verify.check_reviewed_freshness(kit, Path("skill.md"), "2026-09-01", warn)
            self.assertTrue(any("predates the last edit 2026-09-20" in w for w in warn), warn)

    def test_reviewed_after_the_last_commit_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = self.repo(tmp)
            warn: list[str] = []
            kit_verify.check_reviewed_freshness(kit, Path("skill.md"), "2026-09-25", warn)
            self.assertEqual(warn, [])

    def test_a_path_git_has_never_heard_of_is_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = self.repo(tmp)
            warn: list[str] = []
            kit_verify.check_reviewed_freshness(kit, Path("nope.md"), "2020-01-01", warn)
            self.assertEqual(warn, [])

    def test_no_git_repository_is_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            warn: list[str] = []
            kit_verify.check_reviewed_freshness(Path(tmp), Path("skill.md"), "2020-01-01", warn)
            self.assertEqual(warn, [])

    def test_no_git_flag_is_threaded_through_check_unit(self):
        text = ("---\nname: demo\ndescription: Demo skill. Use when testing.\nmetadata:\n"
                "  version: \"1\"\n  updated: \"2026-09-20\"\n  reviewed: \"2026-09-01\"\n---\n\n# demo\n")
        with tempfile.TemporaryDirectory() as tmp:
            kit = self.repo(tmp, "skills/demo/SKILL.md", text)
            saved = kit_verify.KIT
            kit_verify.KIT = kit
            try:
                p, rel = kit / "skills" / "demo" / "SKILL.md", Path("skills/demo/SKILL.md")
                errors, stale, warn = [], [], []
                kit_verify.check_unit(p, rel, errors, stale, 0, None, warn, False)
                self.assertTrue(any("predates the last edit" in w for w in warn), warn)
                errors, stale, warn = [], [], []
                kit_verify.check_unit(p, rel, errors, stale, 0, None, warn, True)
                self.assertEqual([w for w in warn if "predates" in w], [])
            finally:
                kit_verify.KIT = saved


    def test_no_git_cli_flag_is_accepted(self):
        rc, _, err = run("--no-env", "--no-git", str(FIX / "good-skill"))
        self.assertEqual(rc, 0, err)


class LoadingTable(unittest.TestCase):
    """docs/loading.md quotes the unit/description/body numbers between `<!-- kit-verify:<key> -->`
    markers kit-verify itself computes — a mismatch (or a missing marker) is a failure, not a warning, since a
    stale number in a doc is just wrong, not a matter of editorial judgement."""

    def with_doc(self, text: str, fn):
        with tempfile.TemporaryDirectory() as tmp:
            saved = kit_verify.LOADING_MD
            kit_verify.LOADING_MD = Path(tmp) / "loading.md"
            kit_verify.LOADING_MD.write_text(text, encoding="utf-8")
            try:
                errors: list[str] = []
                fn(errors)
                return errors
            finally:
                kit_verify.LOADING_MD = saved

    def marked(self, values: dict[str, str]) -> str:
        return "".join(f"<!-- kit-verify:{k} -->{v}<!-- /kit-verify:{k} -->\n" for k, v in values.items())

    def write_and_read(self, text: str, values: dict[str, str], **kw) -> tuple[str, dict[str, str]]:
        """Write `text` as docs/loading.md, run `write_loading_table(values, **kw)` against it, and return the
        file's contents afterwards plus the notes the call returned."""
        with tempfile.TemporaryDirectory() as tmp:
            saved = kit_verify.LOADING_MD
            kit_verify.LOADING_MD = Path(tmp) / "loading.md"
            kit_verify.LOADING_MD.write_text(text, encoding="utf-8")
            try:
                notes = kit_verify.write_loading_table(values, **kw)
                return kit_verify.LOADING_MD.read_text(encoding="utf-8"), notes
            finally:
                kit_verify.LOADING_MD = saved

    def test_matching_markers_pass(self):
        want = kit_verify.loading_table_values()
        errors = self.with_doc(self.marked(want), kit_verify.check_loading_table_drift)
        self.assertEqual(errors, [])

    def test_a_stale_number_fails(self):
        want = dict(kit_verify.loading_table_values())
        want["units"] = str(int(want["units"]) + 1)
        errors = self.with_doc(self.marked(want), kit_verify.check_loading_table_drift)
        self.assertTrue(any("kit-verify:units" in e and "--loading-table --write" in e for e in errors), errors)

    def test_byte_totals_tolerate_small_drift_but_not_large(self):
        n = kit_verify.loading_numbers()
        near = dict(kit_verify.loading_table_values({**n, "body_bytes": int(n["body_bytes"] * 1.03)}))
        self.assertEqual(self.with_doc(self.marked(near), kit_verify.check_loading_table_drift), [])
        far = dict(kit_verify.loading_table_values({**n, "body_bytes": int(n["body_bytes"] * 1.2)}))
        errors = self.with_doc(self.marked(far), kit_verify.check_loading_table_drift)
        self.assertTrue(any("kit-verify:body-bytes" in e for e in errors), errors)

    def test_write_rewrites_every_marker_and_then_passes(self):
        n = kit_verify.loading_numbers()
        stale = kit_verify.loading_table_values({**n, "units": n["units"] - 1, "body_bytes": n["body_bytes"] // 2})
        def fix_then_check(errors):
            kit_verify.write_loading_table(kit_verify.loading_table_values())
            kit_verify.check_loading_table_drift(errors)
        self.assertEqual(self.with_doc("intro\n" + self.marked(stale), fix_then_check), [])

    def test_write_leaves_an_in_tolerance_byte_total_untouched(self):
        # a byte total drifting inside LOADING_BYTES_TOLERANCE must not touch the doc, or every PR that edits
        # a skill body rewrites the same lines and collides with a parallel one on docs/loading.md.
        n = kit_verify.loading_numbers()
        near = dict(kit_verify.loading_table_values({**n, "body_bytes": int(n["body_bytes"] * 1.03)}))
        doc = self.marked(near)
        out, notes = self.write_and_read(doc, kit_verify.loading_table_values())
        self.assertEqual(out, doc)
        self.assertIn("within 5%, left as is", notes.get("body-bytes", ""))

    def test_write_rewrites_an_out_of_tolerance_byte_total(self):
        n = kit_verify.loading_numbers()
        far = dict(kit_verify.loading_table_values({**n, "body_bytes": int(n["body_bytes"] * 1.2)}))
        doc = self.marked(far)
        out, notes = self.write_and_read(doc, kit_verify.loading_table_values())
        self.assertNotEqual(out, doc)
        self.assertEqual(notes.get("body-bytes"), "(rewritten)")
        errors: list[str] = []
        saved = kit_verify.LOADING_MD
        with tempfile.TemporaryDirectory() as tmp:
            kit_verify.LOADING_MD = Path(tmp) / "loading.md"
            kit_verify.LOADING_MD.write_text(out, encoding="utf-8")
            try:
                kit_verify.check_loading_table_drift(errors)
            finally:
                kit_verify.LOADING_MD = saved
        self.assertEqual(errors, [])

    def test_write_always_rewrites_a_count_mismatch(self):
        n = kit_verify.loading_numbers()
        stale = dict(kit_verify.loading_table_values({**n, "units": n["units"] - 1}))
        doc = self.marked(stale)
        out, notes = self.write_and_read(doc, kit_verify.loading_table_values())
        self.assertNotEqual(out, doc)
        self.assertEqual(notes.get("units"), "(rewritten)")

    def test_force_rewrites_an_in_tolerance_byte_total_too(self):
        n = kit_verify.loading_numbers()
        near = dict(kit_verify.loading_table_values({**n, "body_bytes": int(n["body_bytes"] * 1.03)}))
        doc = self.marked(near)
        out, notes = self.write_and_read(doc, kit_verify.loading_table_values(), force=True)
        self.assertNotEqual(out, doc)
        self.assertEqual(notes.get("body-bytes"), "(rewritten)")

    def test_write_cli_reports_left_as_is_for_in_tolerance_drift(self):
        # in-process (not the `run()` subprocess helper): a subprocess re-imports kit_verify fresh and would
        # read the kit's real docs/loading.md, not this test's temp file
        n = kit_verify.loading_numbers()
        near = dict(kit_verify.loading_table_values({**n, "body_bytes": int(n["body_bytes"] * 1.03)}))
        doc = self.marked(near)
        saved = kit_verify.LOADING_MD
        with tempfile.TemporaryDirectory() as tmp:
            kit_verify.LOADING_MD = Path(tmp) / "loading.md"
            kit_verify.LOADING_MD.write_text(doc, encoding="utf-8")
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = kit_verify.main(["--loading-table", "--write"])
                self.assertEqual(rc, 0)
                self.assertIn("within 5%, left as is", buf.getvalue())
                self.assertEqual(kit_verify.LOADING_MD.read_text(encoding="utf-8"), doc)
            finally:
                kit_verify.LOADING_MD = saved

    def test_missing_markers_are_each_reported(self):
        errors = self.with_doc("no markers here\n", kit_verify.check_loading_table_drift)
        self.assertEqual(len(errors), 4, errors)

    def test_loading_table_cli_prints_every_number(self):
        rc, out, err = run("--loading-table")
        self.assertEqual(rc, 0, err)
        for key in ("units", "desc-bytes", "bodies", "body-bytes"):
            self.assertIn(f"{key}:", out)

    def test_description_budget_warns_past_90_percent(self):
        # the real kit sits at 9,470 / 9,500 B today — past the 90% line kit-verify warns at, not yet over the
        # cap (the whole point: a warning before the surprise failure)
        rc, out, err = run("--no-env")
        self.assertEqual(rc, 0, err)
        self.assertIn("~ warn: descriptions total", err)
        self.assertIn("largest:", err)


if __name__ == "__main__":
    unittest.main()
