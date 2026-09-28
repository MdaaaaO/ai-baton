"""skills/self-assessment/ledger.py — the update-ledger builder: placeholder detection, the `--page`
existence check, and the two `self_assessment.report` modes for a week file's missing report block.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import argparse
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(KIT / "context-db" / "bin"))

import kb  # noqa: E402
import kit_profile  # noqa: E402

LEDGER_PATH = KIT / "skills" / "self-assessment" / "ledger.py"


def load_ledger():
    spec = importlib.util.spec_from_file_location("ledger", LEDGER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


LEDGER = load_ledger()

WEEK_FILE_WEEK = """---
title: Weekly update
type: self-assessment
domain: self-assessment
status: archived
updated: 2027-01-11
---

# Weekly update

## Shipped
- shipped a thing (evidence link)

## Learnings
- learned a thing
"""

LATTICE_WEEK = """---
title: Weekly update
type: self-assessment
domain: self-assessment
status: archived
updated: 2027-01-11
---

# Weekly update

## Shipped
- shipped a thing (evidence link)

## Report update

**What I worked on**
- shipped a thing (evidence link)

**Next week**
- do another thing
"""


class BlankStore(unittest.TestCase):
    """A test never reads the machine's store: kit_profile is pointed at a throw-away blank one (and the
    lru caches cleared) so `make_card`'s store-driven default mode is exercised without touching this
    machine's real env store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kit_profile.ENV_DIR, kb.ENV)
        kit_profile.ENV_DIR = env
        kb.ENV = env
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        kb.init_blank()

    def tearDown(self):
        kit_profile.ENV_DIR, kb.ENV = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()

    def write_week(self, name: str, text: str) -> Path:
        p = Path(self.tmp.name) / name
        p.write_text(text, encoding="utf-8")
        return p


class MissingReportBlock(BlankStore):
    """`self_assessment.report` decides what a missing `## … update` heading means: expected (and
    harmless) in `week-file` mode, a real bug everywhere else."""

    def test_week_file_mode_falls_back_to_the_file_s_own_sections(self):
        week = self.write_week("2027-W01.md", WEEK_FILE_WEEK)
        err = io.StringIO()
        with redirect_stderr(err):
            card = LEDGER.make_card(week, mode="week-file")
        self.assertIn("week-file mode", err.getvalue())
        headings = [s["heading"] for s in card["sections"]]
        self.assertEqual(headings, ["Shipped", "Learnings"])

    def test_lattice_mode_errors_naming_the_heading(self):
        week = self.write_week("2027-W02.md", WEEK_FILE_WEEK)
        with self.assertRaises(SystemExit) as cm:
            LEDGER.make_card(week, mode="lattice")
        self.assertIn("## … update", str(cm.exception))
        self.assertIn("lattice", str(cm.exception))

    def test_lattice_mode_with_the_block_present_still_works(self):
        week = self.write_week("2027-W03.md", LATTICE_WEEK)
        card = LEDGER.make_card(week, mode="lattice")
        headings = [s["heading"] for s in card["sections"]]
        self.assertEqual(headings, ["What I worked on", "Next week"])

    def test_default_mode_comes_from_the_store(self):
        kb.save_config({**kb.load_config(), "self_assessment": {**kb.load_config().get("self_assessment", {}), "report": "week-file"}})
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        week = self.write_week("2027-W04.md", WEEK_FILE_WEEK)
        err = io.StringIO()
        with redirect_stderr(err):
            card = LEDGER.make_card(week)  # no --mode: reads self_assessment.report
        self.assertIn("week-file mode", err.getvalue())
        self.assertEqual([s["heading"] for s in card["sections"]], ["Shipped", "Learnings"])


class PlaceholderCheck(unittest.TestCase):
    """`check_html` used to look for one token (`{{`, the page's own unfilled template placeholder);
    a template placeholder that leaked into a week file's bullets (`<…>`, an ellipsis, `TBD`/`TODO`)
    passed straight through into the published ledger."""

    def page(self, cards: list[dict]) -> str:
        import json
        return (
            '<html><body><script id="data" type="application/json">'
            + json.dumps(cards)
            + "</script></body></html>"
        )

    def card(self, bullet: str) -> dict:
        return {
            "id": "W09", "year": 2027, "dates": "x",
            "sections": [
                {"heading": "A", "bullets": [bullet]},
                {"heading": "B", "bullets": ["fine"]},
                {"heading": "C", "bullets": ["fine"]},
            ],
        }

    def test_angle_bracket_placeholder_is_flagged(self):
        problems = LEDGER.check_html(self.page([self.card("<one sentence describing the win>")]))
        self.assertTrue(any("placeholder" in p for p in problems), problems)

    def test_ellipsis_placeholder_is_flagged(self):
        problems = LEDGER.check_html(self.page([self.card("shipped the thing …")]))
        self.assertTrue(any("placeholder" in p for p in problems), problems)

    def test_tbd_and_todo_are_flagged(self):
        for bullet in ("TBD", "TODO: fill this in"):
            problems = LEDGER.check_html(self.page([self.card(bullet)]))
            self.assertTrue(any("placeholder" in p for p in problems), (bullet, problems))

    def test_ordinary_bullet_is_not_flagged(self):
        problems = LEDGER.check_html(self.page([self.card("shipped a thing (evidence link)")]))
        self.assertEqual([p for p in problems if "placeholder" in p], [])


class PageExistenceCheck(unittest.TestCase):
    """The `--page` argument names a local file the SKILL's step 10 saved from `self_assessment.ledger_url`
    via `Artifact read`; a stale URL must fail with a clear message, not an unguarded `FileNotFoundError`
    at the last step of a long skill run."""

    def test_missing_page_file_exits_cleanly_naming_the_store_key(self):
        args = argparse.Namespace(init=False, page=str(Path(tempfile.mkdtemp()) / "missing-index.html"))
        with self.assertRaises(SystemExit) as cm:
            LEDGER.load_page(args)
        msg = str(cm.exception)
        self.assertNotIn("Traceback", msg)
        self.assertIn("self_assessment.ledger_url", msg)


if __name__ == "__main__":
    unittest.main()
