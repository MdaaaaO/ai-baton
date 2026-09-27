"""kb.py against a throw-away env store: set/get/rm, renamed kinds, provenance + stale, config, migrate, blank
config vs the template. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "bin"))

import kb  # noqa: E402
import kit_profile  # noqa: E402
import kit_verify  # noqa: E402


class StoreCase(unittest.TestCase):
    """Every test gets its own blank store; kb/kit_profile/kit_verify are pointed at it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR)
        kb.ENV = self.env
        kb.CTX = Path(self.tmp.name)
        kb.ROOT = Path(self.tmp.name).parent
        kit_profile.ENV_DIR = self.env
        kb.init_blank()

    def tearDown(self):
        kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR = self._saved
        self.tmp.cleanup()

    def cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = kb.main(["kb.py", *args]) or 0
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
        return rc, out.getvalue(), err.getvalue()


class InitAndConfig(StoreCase):
    def test_blank_store_has_config_and_one_doc_per_system(self):
        self.assertTrue((self.env / "config.json").is_file())
        for system in kb.DEFAULT_KINDS:
            self.assertTrue((self.env / f"{system}.md").is_file(), system)
        self.assertEqual(kb.init_blank(), [])  # never overwrites

    def test_blank_config_satisfies_kit_verify_and_needs_no_migration(self):
        errors: list[str] = []
        kit_verify.check_env_store(errors)
        self.assertEqual(errors, [])
        cfg = kb.load_config()
        self.assertEqual(kb.migrate_config(cfg), [])
        self.assertEqual(set(cfg["systems"]), set(kb.SYSTEMS))

    def test_template_keys_cover_blank_config(self):
        tmpl = json.loads((KIT / "environment-template" / "config.json").read_text(encoding="utf-8"))
        blank = {k for k in kb.blank_config() if not k.startswith("_")}
        want = {k for k in tmpl if not k.startswith("_")}
        self.assertTrue(blank <= want, blank - want)
        self.assertEqual(set(tmpl["systems"]), set(kb.SYSTEMS))

    def test_config_set_and_get_round_trip_json_and_strings(self):
        rc, out, _ = self.cli("config-set", "systems.slack", "true")
        self.assertEqual(rc, 0)
        self.assertIs(kb.load_config()["systems"]["slack"], True)
        self.cli("config-set", "github.org", "acme-org")
        rc, out, _ = self.cli("config", "github.org")
        self.assertEqual((rc, out.strip()), (0, "acme-org"))
        self.cli("config-set", "labels", '{"shared": ["bug"]}')
        self.assertEqual(kb.load_config()["labels"], {"shared": ["bug"]})

    def test_dotted_helpers(self):
        cfg = {"a": {"b": 1}}
        self.assertEqual(kb.dotted_get(cfg, "a.b"), 1)
        self.assertIsNone(kb.dotted_get(cfg, "a.c"))
        kb.dotted_set(cfg, "a.c.d", 2)
        self.assertEqual(cfg["a"]["c"]["d"], 2)
        with self.assertRaises(SystemExit):
            kb.dotted_set(cfg, "a.b.x", 3)  # `b` is not an object


class Rows(StoreCase):
    def test_same_value_with_an_explicit_from_is_re_verified(self):
        # re-running a discovery on a still-valid fact must re-date its provenance, or `stale` never clears
        old = (dt.date.today() - dt.timedelta(days=400)).isoformat()
        kb.set_fact("slack", "channel", "eng-help", "C1", "help", f"tool:slack_search_channels {old}")
        self.assertEqual(len(kb.stale_rows()), 1)
        doc = self.env / "slack.md"
        doc.write_text(doc.read_text(encoding="utf-8").replace(f"updated: {kb.today()}", "updated: 2020-01-01"), encoding="utf-8")
        self.assertEqual(kb.set_fact("slack", "channel", "eng-help", "C1"), "unchanged")  # no --from: nothing to say
        self.assertIn("updated: 2020-01-01", doc.read_text(encoding="utf-8"))
        self.assertEqual(kb.set_fact("slack", "channel", "eng-help", "C1", "", "tool:slack_search_channels"), "re-verified")
        row = kb.get_row("slack", "channel", "eng-help")
        self.assertEqual((row["value"], row["purpose"]), ("C1", "help"))
        self.assertEqual(row["learned-from"], f"tool:slack_search_channels {kb.today()}")
        self.assertIn(f"updated: {kb.today()}", doc.read_text(encoding="utf-8"))
        self.assertEqual(kb.stale_rows(), [])
        self.assertEqual(kb.set_fact("slack", "channel", "eng-help", "C1", "", "tool:slack_search_channels"), "unchanged")  # same provenance, same day
        rc, out, _ = self.cli("set", "slack.channel", "eng-help", "C1", "--from", "user")
        self.assertEqual((rc, out.strip()), (0, "re-verified slack.channel eng-help"))

    def test_empty_name_is_a_usage_error_and_writes_nothing(self):
        # `kb.py set github.person "" x` printed `added github.person ` and persisted nothing
        before = (self.env / "github.md").read_text(encoding="utf-8")
        for bad in ("", "   ", " alice", "a|b"):
            rc, out, err = self.cli("set", "github.person", bad, "x")
            self.assertEqual(rc, 2, bad)
            self.assertEqual(out, "")
            self.assertIn("row name", err)
        self.assertEqual((self.env / "github.md").read_text(encoding="utf-8"), before)
        rc, _, err = self.cli("rm", "github.person", "")
        self.assertEqual(rc, 2)
        rc, _, err = self.cli("set", "github.bad kind", "alice", "x")
        self.assertEqual(rc, 2)
        self.assertIn("bad kind", err)

    def test_set_get_rm(self):
        self.assertEqual(kb.set_fact("slack", "channel", "eng-help", "C-ID-1", "the help channel", "tool:slack_search_channels"), "added")
        self.assertEqual(kb.get("slack", "channel", "eng-help"), "C-ID-1")
        row = kb.get_row("slack", "channel", "eng-help")
        self.assertEqual(row["purpose"], "the help channel")
        self.assertTrue(row["learned-from"].startswith("tool:slack_search_channels 20"))
        self.assertEqual(kb.set_fact("slack", "channel", "eng-help", "C-ID-2"), "updated")
        self.assertEqual(kb.get_row("slack", "channel", "eng-help")["purpose"], "the help channel")  # kept
        self.assertEqual(kb.set_fact("slack", "channel", "eng-help", "C-ID-2"), "unchanged")
        self.assertTrue(kb.rm_fact("slack", "channel", "eng-help"))
        self.assertIsNone(kb.get("slack", "channel", "eng-help"))
        self.assertFalse(kb.rm_fact("slack", "channel", "eng-help"))

    def test_new_kind_and_new_system_get_their_sections(self):
        kb.set_fact("slack", "bot", "reviewer", "B1")
        kb.set_fact("airflow", "path", "alerts-kb", "/x/y")
        self.assertEqual(kb.get("slack", "bot", "reviewer"), "B1")
        self.assertEqual(kb.get("airflow", "path", "alerts-kb"), "/x/y")
        self.assertIn("## path", (self.env / "airflow.md").read_text(encoding="utf-8"))
        self.assertIn("tags: [env, airflow]", (self.env / "airflow.md").read_text(encoding="utf-8"))

    def test_pipes_in_cells_are_escaped(self):
        kb.set_fact("tracker", "query", "mine", "project = X | resolved", "a|b")
        row = kb.get_row("tracker", "query", "mine")
        self.assertEqual((row["value"], row["purpose"]), ("project = X | resolved", "a|b"))

    def test_bad_names_are_refused(self):
        with self.assertRaises(SystemExit):
            kb.doc_path("Slack")
        with self.assertRaises(SystemExit):
            kb.set_fact("slack", "bad kind", "x", "y")
        self.assertEqual(kb.split_key("slack.channel"), ("slack", "channel"))
        with self.assertRaises(SystemExit):
            kb.split_key("slack")

    def test_cli_get_set_rm_list_values(self):
        rc, out, _ = self.cli("set", "github.person", "alice", "alice-login", "--purpose", "reviewer", "--from", "user")
        self.assertEqual((rc, out.strip()), (0, "added github.person alice"))
        rc, out, _ = self.cli("get", "github.person", "alice")
        self.assertEqual((rc, out.strip()), (0, "alice-login"))
        rc, out, err = self.cli("get", "github.person", "nobody")
        self.assertEqual(rc, 1)
        self.assertIn("kb.py discover github.person 'nobody'", err)
        rc, out, _ = self.cli("list", "github")
        self.assertIn("github.person\talice\talice-login\treviewer\tuser ", out)
        rc, out, _ = self.cli("values")
        self.assertEqual(out.split(), ["alice-login"])
        rc, out, _ = self.cli("rm", "github.person", "alice")
        self.assertEqual((rc, out.strip()), (0, "removed github.person alice"))
        rc, _, _ = self.cli("rm", "github.person", "alice")
        self.assertEqual(rc, 1)

    def test_free_text_provenance_is_refused_and_dated_forms_accepted(self):
        with self.assertRaises(SystemExit):
            kb.normalize_provenance("found it in a thread")
        for who in ("user", "tool:slack_search_channels", "derived:tracker.key_regex"):
            self.assertRegex(kb.normalize_provenance(who), rf"^{who} \d{{4}}-\d{{2}}-\d{{2}}$")
        with self.assertRaises(SystemExit):  # the retired migration's provenance word is no longer accepted for new rows
            kb.normalize_provenance("import:acme")
        self.assertEqual(kb.normalize_provenance("user 2026-01-01"), "user 2026-01-01")  # a given date is kept


class RenamedKinds(StoreCase):
    def write_legacy_channels(self):
        p = self.env / "slack.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n## channels\n\n" + kb.table_header() + "\n| old | C-OLD | legacy | user 2026-01-01 |\n", encoding="utf-8")

    def test_get_and_set_answer_through_the_canonical_kind(self):
        self.write_legacy_channels()
        self.assertEqual(kb.canonical_kind("slack", "channels"), "channel")
        self.assertEqual(kb.get("slack", "channels", "old"), "C-OLD")
        self.assertEqual(kb.get("slack", "channel", "old"), "C-OLD")
        self.assertEqual(kb.set_fact("slack", "channels", "new", "C-NEW"), "added")
        self.assertEqual(kb.get("slack", "channel", "new"), "C-NEW")

    def test_migrate_moves_rows_and_verify_flags_a_store_carrying_one(self):
        self.write_legacy_channels()
        errors: list[str] = []
        kit_verify.check_env_store(errors)
        self.assertTrue(any("`## channels` belong under `## channel`" in e for e in errors), errors)
        pending = kb.migrate_kinds(apply=False)
        self.assertEqual(pending, ["slack.channels 'old' → slack.channel"])
        self.assertEqual(kb.get_row("slack", "channels", "old")["value"], "C-OLD")  # dry run moved nothing
        kb.migrate_kinds(apply=True)
        _, facts = kb.parse_doc("slack")
        self.assertNotIn("channels", facts)
        self.assertEqual(facts["channel"]["old"]["learned-from"], "user 2026-01-01")  # provenance carried over
        errors = []
        kit_verify.check_env_store(errors)
        self.assertEqual(errors, [])

    def test_rm_and_list_resolve_the_renamed_kind_and_rm_bumps_updated(self):
        # `rm slack.channels x` failed on a row written as `slack.channel x`; rm never bumped updated:
        self.write_legacy_channels()
        kb.set_fact("slack", "channel", "canon", "C-CANON")
        rc, out, _ = self.cli("list", "slack.channels")
        self.assertIn("slack.channels\told\tC-OLD", out)
        self.assertIn("slack.channel\tcanon\tC-CANON", out)  # both spellings list rows under either heading
        rc, out, _ = self.cli("list", "slack.channel")
        self.assertIn("old\tC-OLD", out)
        doc = self.env / "slack.md"
        doc.write_text(doc.read_text(encoding="utf-8").replace(f"updated: {kb.today()}", "updated: 2020-01-01"), encoding="utf-8")
        self.assertTrue(kb.rm_fact("slack", "channel", "old"))       # canonical spelling removes the legacy row
        self.assertIsNone(kb.get("slack", "channel", "old"))
        self.assertIn(f"updated: {kb.today()}", doc.read_text(encoding="utf-8"))
        self.assertTrue(kb.rm_fact("slack", "channels", "canon"))    # legacy spelling removes the canonical row
        self.assertIsNone(kb.get("slack", "channel", "canon"))
        self.assertEqual(kb.kind_aliases("slack", "channels"), ["channel", "channels"])
        self.assertEqual(kb.kind_aliases("slack", "channel"), ["channel", "channels"])
        self.assertEqual(kb.kind_aliases("slack", "user"), ["user"])

    def test_rm_removes_one_row_in_the_conflict_case(self):
        # the name under BOTH headings (what migrate reports as CONFLICT): `rm` drops exactly the heading as spelled,
        # so the migrate hint `kb.py rm slack.channels old` keeps the verified canonical row
        self.write_legacy_channels()
        kb.set_fact("slack", "channel", "old", "C-CANON")
        self.assertTrue(kb.rm_fact("slack", "channels", "old"))
        facts = kb.parse_doc("slack")[1]
        self.assertNotIn("old", facts.get("channels", {}))
        self.assertEqual(facts["channel"]["old"]["value"], "C-CANON")
        self.assertEqual(kb.migrate_kinds(apply=False), [])  # the conflict is gone, nothing pending
        self.write_legacy_channels()
        rc, out, _ = self.cli("rm", "slack.channel", "old")  # spelled canonical: the canonical row goes, the legacy stays
        self.assertEqual(rc, 0)
        self.assertIn("removed slack.channel old (a row under ## channels still holds the name", out)  # said, not silent
        facts = kb.parse_doc("slack")[1]
        self.assertNotIn("old", facts.get("channel", {}))
        self.assertEqual(facts["channels"]["old"]["value"], "C-OLD")
        self.assertEqual(kb.rm_fact_report("slack", "channels", "old"), ("channels", []))
        rc, out, _ = self.cli("rm", "slack.channel", "gone")
        self.assertEqual((rc, out.strip()), (1, "no such row slack.channel gone"))

    def test_conflict_is_reported_and_left(self):
        self.write_legacy_channels()
        kb.set_fact("slack", "channel", "old", "C-DIFFERENT")
        done = kb.migrate_kinds(apply=True)
        self.assertTrue(done[0].startswith("CONFLICT slack.channels 'old'"), done)
        self.assertEqual(kb.parse_doc("slack")[1]["channels"]["old"]["value"], "C-OLD")


class Stale(StoreCase):
    def test_tool_rows_age_user_rows_never(self):
        old = (dt.date.today() - dt.timedelta(days=400)).isoformat()
        kb.set_fact("slack", "channel", "aged", "C1", "", f"tool:slack_search_channels {old}")
        kb.set_fact("slack", "channel", "fresh", "C2", "", "tool:slack_search_channels")
        kb.set_fact("slack", "channel", "said", "C3", "", f"user {old}")
        stale = {r["name"]: r for r in kb.stale_rows()}
        self.assertEqual(set(stale), {"aged"})
        self.assertTrue(stale["aged"]["discoverable"])  # slack.channel has a manifest
        self.assertIsNone(kb.staleness("slack", "channel", "said", kb.get_row("slack", "channel", "said")))
        self.assertEqual({r["name"] for r in kb.stale_rows(ttl_override=0)}, set())  # 0 = never
        self.assertEqual({r["name"] for r in kb.stale_rows(ttl_override=1)}, {"aged"})

    def test_undated_legacy_row_is_stale(self):
        kb.set_fact("slack", "channel", "legacy", "C9", "", "grep in the old profile", keep_learned=True)
        rows = kb.stale_rows()
        self.assertEqual([(r["name"], r["age"]) for r in rows], [("legacy", None)])
        rc, out, _ = self.cli("stale", "--check")
        self.assertEqual(rc, 3)
        self.assertIn("undated", out)


class MigrateConfig(StoreCase):
    def test_null_systems_migrates_and_lists_off_units(self):
        # kit-verify's remedy for `systems: null` is `kb.py migrate` — which must not traceback on that input
        cfg = {"systems": None}
        done = kb.migrate_config(cfg)
        self.assertEqual(set(cfg["systems"]), set(kb.SYSTEMS))
        self.assertEqual(len(done), len(kb.SYSTEMS))
        self.assertTrue(kb.unmet_units({"systems": None}))  # every gated unit is off; no AttributeError
        kb.save_config({**kb.load_config(), "systems": None})
        rc, out, err = self.cli("migrate", "--check")
        self.assertEqual(rc, 3, err)
        rc, out, err = self.cli("migrate")
        self.assertEqual(rc, 0, err)
        self.assertEqual(set(kb.load_config()["systems"]), set(kb.SYSTEMS))

    def test_non_dict_systems_is_a_one_line_error(self):
        # a truthy non-dict `systems` (a list, a string) reached `dict(systems)` / `.get` — kit-verify's type
        # error is the answer here too, one line and exit 1, never a traceback
        for bad in (["slack"], "yes"):
            with self.assertRaises(SystemExit) as cm:
                kb.migrate_config({"systems": bad})
            self.assertIn("systems must be an object of true/false flags", str(cm.exception))
            with self.assertRaises(SystemExit):
                kb.unmet_units({"systems": bad})
        kb.save_config({**kb.load_config(), "systems": ["slack"]})
        self.assertEqual(self.cli("migrate")[0], 1)
        self.assertEqual(self.cli("migrate", "--off")[0], 1)
        r = subprocess.run([sys.executable, str(KIT / "context-db" / "bin" / "kb.py"), "migrate"], capture_output=True, text=True,
                           env={**os.environ, "CONTEXT_ROOT": str(kb.ENV.parents[1])})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("systems must be an object of true/false flags", r.stderr)

    def test_null_self_assessment_does_not_crash(self):
        cfg = {"systems": {}, "self_assessment": None}
        kb.migrate_config(cfg)
        self.assertEqual(set(cfg["systems"]), set(kb.SYSTEMS))
        cfg = {"systems": {}, "self_assessment": {"sources": None}}
        kb.migrate_config(cfg)

    def test_blank_config_and_template_are_one_key_set(self):
        # three sources of truth → kb owns the optional set, the template documents every key,
        # config_key_drift() fails kit-verify when blank_config() and the template disagree
        self.assertEqual(kb.config_key_drift(), [])
        self.assertIs(kit_verify.OPTIONAL, kb.OPTIONAL_CONFIG_KEYS)
        saved = kb.blank_config
        try:
            kb.blank_config = lambda: {k: v for k, v in saved().items() if k != "labels"} | {"extra": 1}
            drift = kb.config_key_drift()
        finally:
            kb.blank_config = saved
        self.assertTrue(any("template key 'labels' missing from kb.blank_config()" in d for d in drift), drift)
        self.assertTrue(any("key 'extra' is not in environment-template" in d for d in drift), drift)

    def test_renamed_flag_moves_and_missing_flags_seed(self):
        cfg = {"systems": {"snowflake": True, "slack": True}, "self_assessment": {"sources": ["snowflake"]}}
        done = kb.migrate_config(cfg)
        self.assertIs(cfg["systems"]["datalake"], True)
        self.assertNotIn("snowflake", cfg["systems"])
        self.assertIs(cfg["systems"]["airflow"], True)   # seeded from the legacy flag
        self.assertIs(cfg["systems"]["notion"], False)
        self.assertEqual(cfg["self_assessment"]["sources"], ["datalake"])
        self.assertTrue(any("systems.snowflake → systems.datalake" in d for d in done), done)
        self.assertEqual(kb.migrate_config(cfg), [])

    def test_cli_migrate_check_reports_pending_and_off_lists_units(self):
        cfg = kb.load_config()
        del cfg["systems"]["notion"]
        kb.save_config(cfg)
        rc, out, _ = self.cli("migrate", "--check")
        self.assertEqual(rc, 3)
        self.assertIn("pending: systems.notion added", out)
        rc, out, _ = self.cli("migrate")
        self.assertEqual(rc, 0)
        self.assertIs(kb.load_config()["systems"]["notion"], False)
        rc, out, _ = self.cli("migrate", "--off")
        self.assertEqual(rc, 0)
        self.assertIn("notion-page-review (notion)", out)
        self.cli("config-set", "systems.notion", "true")
        rc, out, _ = self.cli("migrate", "--off")
        self.assertNotIn("notion-page-review", out)


class Projection(StoreCase):
    def test_digit_values_keep_leading_zeros(self):
        # `int("0123")` dropped the zeros of an id; a digit string projects to int only when that round-trips
        kb.set_fact("tracker", "transition", "in_review", "007")
        kb.set_fact("tracker", "transition", "done", "31")
        kb.set_fact("slack", "channel", "zero", "0123")
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        try:
            cfg = kit_profile.load()
        finally:
            kit_profile.env_config.cache_clear()
            kit_profile.load.cache_clear()
        self.assertEqual(cfg["tracker"]["transitions"], {"in_review": "007", "done": 31})
        self.assertEqual(cfg["slack"]["channels"]["zero"], "0123")


class Discover(StoreCase):
    def test_cli_plan_provenance_is_always_valid(self):
        # a `cli` command whose first word is an unfilled placeholder produced `--from tool:<config.x` (rejected)
        self.assertEqual(kb.cli_provenance("gh api x", "gh api x"), "tool:gh")
        self.assertEqual(kb.cli_provenance("{config.datalake.cli} query", "<datalake.cli unset — kb.py discover datalake.cli> query"), "tool:cli")
        self.assertEqual(kb.cli_provenance("", ""), "tool:cli")
        saved = kb.find_fact
        kb.find_fact = lambda key, name="", cfg=None: ({"system": "datalake", "_file": "x"},
                                                       {"key": "datalake.tool", "target": "row", "tool": "cli", "verify": "v",
                                                        "args": {"cmd": "{config.datalake.cli} --version"}})
        try:
            plan = kb.discover_plan("datalake.tool", "sql", kb.load_config())
        finally:
            kb.find_fact = saved
        write = [ln for ln in plan.splitlines() if ln.startswith("write:")][0]
        prov = write.split("--from ")[1]
        self.assertEqual(prov, "tool:cli")
        self.assertRegex(kb.normalize_provenance(prov), r"^tool:cli \d{4}-")

    def test_cli_plan_quotes_the_name_for_the_shell(self):
        # #131: the name comes from a NEEDS line an untrusted surface may have supplied — it is data in the printed command
        import shlex
        hostile = "x;$(touch PWNED) `id` y"
        cfg = kb.load_config()
        plan = kb.discover_plan("github.person", hostile, cfg)
        run = [ln for ln in plan.splitlines() if ln.startswith("run:")][0][len("run:"):].strip()
        self.assertEqual(shlex.split(run), ["gh", "api", f"users/{hostile}", "--jq", ".name"])
        argv = [ln for ln in plan.splitlines() if ln.startswith("argv:")][0][len("argv:"):].strip()
        self.assertEqual(json.loads(argv), ["gh", "api", f"users/{hostile}", "--jq", ".name"])
        verify = [ln for ln in plan.splitlines() if ln.startswith("verify:")][0]
        self.assertIn(shlex.quote(hostile), verify)
        self.assertIn(f"kb.py set github.person {shlex.quote(hostile)} <value>", plan)
        plain = kb.discover_plan("github.person", "octocat", cfg)
        self.assertIn("run:     gh api users/octocat --jq .name", plain)  # a safe name stays unquoted
        # a nested row reference selects the row by the raw name, then quotes the row's value
        kb.set_fact("aws", "profile", "prod", "it's-prod", "p", "user")
        cfg["systems"]["aws"] = True
        aws = kb.discover_plan("aws.account", "prod", cfg)
        self.assertIn("--profile 'it'\"'\"'s-prod'", aws)
        # review of #208: a `}` in the name must not close the nested reference and leave the rest unquoted
        for evil in ("x} $(touch PWNED) x", "{config.github.org}"):
            for key in ("aws.account", "github.person"):
                plan = kb.discover_plan(key, evil, cfg)
                run = [ln for ln in plan.splitlines() if ln.startswith("run:")][0][len("run:"):].strip()
                words = shlex.split(run)
                self.assertTrue(any(evil in w for w in words), (key, run))
                self.assertFalse(any("$(touch" in w and evil not in w for w in words), (key, run))
                self.assertEqual(json.loads([ln for ln in plan.splitlines() if ln.startswith("argv:")][0][5:]), words)

    def test_discover_refuses_control_characters(self):
        with self.assertRaises(SystemExit) as cm:
            kb.discover_plan("github.person", "a\nrun: rm -rf ~", kb.load_config())
        self.assertIn("control character", str(cm.exception))

    def test_manifests_validate_and_plans_render(self):
        self.assertEqual(kb.validate_manifests(), [])
        self.assertIsNotNone(kb.find_fact("slack.channel", "eng-help"))
        self.assertIsNone(kb.find_fact("teletext.page", "100"))
        cfg = kb.load_config()
        plan = kb.discover_plan("slack.channel", "eng-help", cfg)
        self.assertIn("NOT APPLICABLE here — systems.slack is not true", plan)
        cfg["systems"]["slack"] = True
        plan = kb.discover_plan("slack.channel", "eng-help", cfg)
        self.assertNotIn("NOT APPLICABLE", plan)
        self.assertIn("call:    slack_search_channels", plan)
        self.assertIn("kb.py set slack.channel eng-help <value>", plan)
        rc, out, _ = self.cli("discover", "--check")
        self.assertEqual(rc, 0)
        self.assertIn("OK —", out)


if __name__ == "__main__":
    unittest.main()


class ConfigSetShapes(StoreCase):
    """config-set refuses what the template says is wrong; readers survive a null section (--force)."""

    def test_refused_shapes_and_keys(self):
        for key, val in (("systems", "notanobject"), ("systems.slack", "yes"), ("systems.nosuch", "true"),
                         ("tracker", "null"), ("trackr.kind", "github"), ("domains", '"one"'),
                         ("systems", '{"jirra": true}'), ("systems", '{"slack": "yes"}')):
            rc, _out, err = self.cli("config-set", key, val)
            self.assertEqual(rc, 2, (key, val, err))
            self.assertIn("config-set refused", err)
        self.assertEqual(kb.load_config().get("systems", {}).get("slack"), False)  # nothing was written

    def test_accepted_values(self):
        for key, val in (("systems.slack", "true"), ("environment", "ci"), ("leaks.markers", '["x"]'),
                         ("cost.columns.user", '"u"'), ("tracker.transitions.done", "31"), ("tracker.key_regex", "(#\\d+)")):
            rc, _out, err = self.cli("config-set", key, val)
            self.assertEqual(rc, 0, (key, val, err))

    def test_force_writes_and_readers_survive_a_null_section(self):
        rc, _out, err = self.cli("config-set", "tracker", "null", "--force")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(kb.load_config()["tracker"])
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        try:
            self.assertIsNone(kit_profile.get("tracker.kind"))
            self.assertIsInstance(kit_profile.load(), dict)
        finally:
            kit_profile.env_config.cache_clear()
            kit_profile.load.cache_clear()


class NoSharedMutation(StoreCase):
    """The cached config is never shared with callers; `set` compares what the table will hold."""

    def reset(self):
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()

    def test_a_caller_editing_the_config_does_not_change_the_next_read(self):
        self.cli("config-set", "tracker.kind", "github")
        self.reset()
        try:
            first = kit_profile.load()
            first["tracker"]["kind"] = "edited"
            first.setdefault("systems", {})["slack"] = True
            kit_profile.env_config()["tracker"] = None
            got = kit_profile.get("tracker")
            got["kind"] = "edited-too"
            self.assertEqual(kit_profile.load()["tracker"]["kind"], "github")
            self.assertEqual(kit_profile.get("tracker.kind"), "github")
            self.assertIs(kit_profile.load().get("systems", {}).get("slack"), False)
            self.assertIsInstance(kit_profile.env_config()["tracker"], dict)
        finally:
            self.reset()

    def test_set_with_surrounding_spaces_is_unchanged_on_rerun(self):
        ch = "C0" + "AB12CD3"  # assembled: fact-shaped
        self.assertEqual(kb.set_fact("slack", "channel", "eng", f" {ch} ", learned="user"), "added")
        self.assertEqual(kb.get("slack", "channel", "eng"), ch)
        self.assertEqual(kb.set_fact("slack", "channel", "eng", f" {ch} "), "unchanged")
        self.assertEqual(kb.set_fact("slack", "channel", "eng", ch), "unchanged")
