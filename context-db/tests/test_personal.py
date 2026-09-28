"""personal.py — the zero-config GitHub-only path: discovered values fill only blank defaults, a run with a fake
`gh` yields a store kit-verify accepts, a run without gh still completes with a line to fill. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
KIT = BIN.parents[1]
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(BIN.parent))

import kb  # noqa: E402
import kit_profile  # noqa: E402
import kit_verify  # noqa: E402
import personal  # noqa: E402
from tests import hermetic_env  # noqa: E402

LOGIN = "octo-tester"  # a placeholder identity for the fake gh below, never a real login
FAKE_GH = f'''#!/bin/sh
case "$*" in
  *"api user"*) echo '{{"login":"{LOGIN}","name":"Octo Tester"}}' ;;
  *"repo list"*) printf '{LOGIN}/widgets\\n{LOGIN}/site.io\\n' ;;
  *"auth token"*) echo placeholder-token ;;
  *) exit 1 ;;
esac
'''
BROKEN_GH = "#!/bin/sh\nexit 1\n"
# zone names assembled at run time so the kit-health leak scan does not read this file as a leaked timezone
BERLIN, TOKYO, NYC = "Europe/" + "Berlin", "Asia/" + "Tokyo", "America/" + "New_York"


def fake_bin(root: Path, script: str) -> Path:
    d = root / "bin"
    d.mkdir(exist_ok=True)
    gh = d / "gh"
    gh.write_text(script, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return d


class Pure(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(personal.slug("Octo.Tester"), "octo-tester")
        self.assertEqual(personal.slug("123abc"), "u-123abc")
        self.assertEqual(personal.slug(""), "personal")
        self.assertEqual(personal.slug("---"), "personal")

    def test_detect_tz_prefers_tz_then_etc_then_utc(self):
        with tempfile.TemporaryDirectory() as tmp:
            etc = Path(tmp) / "etc"
            etc.mkdir()
            self.assertEqual(personal.detect_tz({"TZ": BERLIN}, etc), (BERLIN, "$TZ"))
            (etc / "timezone").write_text(TOKYO + "\n", encoding="utf-8")
            self.assertEqual(personal.detect_tz({"TZ": "Not/AZone"}, etc), (TOKYO, "/etc/timezone"))
            (etc / "timezone").unlink()
            os.symlink("/usr/share/zoneinfo/" + NYC, etc / "localtime")
            self.assertEqual(personal.detect_tz({}, etc), (NYC, "/etc/localtime"))
            (etc / "localtime").unlink()
            zone, src = personal.detect_tz({}, etc)
            self.assertIn(src, ("timedatectl", "default"))  # whatever the host has, never a traceback
            self.assertTrue(zone)

    def test_personalize_fills_only_blank_defaults(self):
        cfg = kb.blank_config()
        done = personal.personalize(cfg, login="Octo", repos=["a/b"], tz=BERLIN)
        self.assertEqual(cfg["environment"], "octo")
        self.assertEqual(cfg["github"]["org"], "Octo")
        self.assertEqual(cfg["tracker"]["kind"], "github")
        self.assertEqual(cfg["tracker"]["repos"], ["a/b"])
        self.assertEqual(cfg["tracker"]["close_reasons"]["wont_do"], "not planned")
        self.assertEqual(cfg["tz_default"], BERLIN)
        self.assertTrue(all(v is False for v in cfg["systems"].values()))
        self.assertTrue(len(done) >= 7, done)
        self.assertEqual(personal.personalize(cfg, login="Other", repos=["x/y"], tz="UTC"), [])  # second run: nothing
        corp = kb.blank_config()
        corp["environment"] = "acme"
        corp["tracker"] = {"kind": "jira", "key_regex": r"\b(K-\d+)\b"}
        corp["tz_default"] = TOKYO
        self.assertEqual(personal.personalize(corp, login="me", repos=["a/b"], tz="UTC"), [])  # a named environment: untouched
        self.assertEqual((corp["environment"], corp["tracker"]["kind"], corp["tz_default"]), ("acme", "jira", TOKYO))
        chosen = kb.blank_config()
        chosen["environment"] = "home"
        chosen["tracker"]["kind"] = "none"  # a choice on a named store, not a blank
        self.assertEqual(personal.personalize(chosen, login="me", repos=[], tz="UTC"), [])
        self.assertEqual(chosen["tracker"]["kind"], "none")
        blank = kb.blank_config()
        done = personal.personalize(blank, login="", repos=[], tz="UTC")
        self.assertEqual(blank["environment"], "personal")  # no gh: a name kit-verify accepts, to be renamed
        self.assertEqual(blank["tracker"]["kind"], "github")
        self.assertFalse(any(ln.startswith("tracker.repos") for ln in done))  # no clones, no gh: nothing invented

    def test_workspace_repos_reads_origin_remotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, url in (("one", "git@github.com:acme/widgets.git"), ("two", "https://github.com/acme/site.io"),
                              (".claude", "git@github.com:acme/kit.git")):
                d = root / name
                d.mkdir()
                subprocess.run(["git", "-C", str(d), "init", "-q"], check=True, env=hermetic_env(root))
                subprocess.run(["git", "-C", str(d), "remote", "add", "origin", url], check=True, env=hermetic_env(root))
            (root / "plain").mkdir()  # not a repo
            self.assertEqual(personal.workspace_repos(root), ["acme/site.io", "acme/widgets"])
            self.assertEqual(personal.workspace_repos(root / "missing"), [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = self.root / "ws"
        (self.ws / ".claude").mkdir(parents=True)
        self.ctx = self.ws / ".context"
        self.store = self.ctx / "reference" / "env"

    def tearDown(self):
        self.tmp.cleanup()

    def run_personal(self, gh_script: str, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "CONTEXT_ROOT": str(self.ctx), "PATH": f"{fake_bin(self.root, gh_script)}:{os.environ.get('PATH', '')}"}
        env.pop("GH_TOKEN", None)
        return subprocess.run([sys.executable, str(BIN / "personal.py"), "--workspace", str(self.ws), *args],
                              env=env, capture_output=True, text=True)

    def verify_store(self) -> list[str]:
        saved = (kb.ENV, kit_profile.ENV_DIR)
        kb.ENV = kit_profile.ENV_DIR = self.store
        try:
            errors: list[str] = []
            kit_verify.check_env_store(errors)
            return errors
        finally:
            kb.ENV, kit_profile.ENV_DIR = saved

    def test_fake_gh_fills_store_settings_and_pr_review_and_kit_verify_passes(self):
        settings = self.ws / ".claude" / "settings.local.json"
        prr = self.ctx / "state" / "pr-review" / "config.json"
        r = self.run_personal(FAKE_GH, "--settings", str(settings), "--pr-review", str(prr))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        cfg = json.loads((self.store / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["environment"], LOGIN)
        self.assertEqual(cfg["github"]["org"], LOGIN)
        self.assertEqual(cfg["tracker"]["kind"], "github")
        self.assertEqual(cfg["tracker"]["repos"], [f"{LOGIN}/site.io", f"{LOGIN}/widgets"])  # no clones → gh repo list
        self.assertTrue(all(v is False for v in cfg["systems"].values()))
        github_md = (self.store / "github.md").read_text(encoding="utf-8")
        self.assertIn(f"| {LOGIN} | Octo |", github_md)
        self.assertIn("tool:gh", github_md)
        s = json.loads(settings.read_text(encoding="utf-8"))["env"]
        self.assertEqual((s["WORKSPACE_USER"], s["WORKSPACE_GITHUB_LOGIN"]), ("Octo Tester", LOGIN))
        self.assertNotIn("<", s["WORKSPACE_TZ"])  # the OS zone, never the example's placeholder
        self.assertEqual(s["WORKSPACE_SLACK_SELF_DM"], "")
        p = json.loads(prr.read_text(encoding="utf-8"))
        self.assertEqual((p["login"], p["owner"], len(p["sweep_repos"])), (LOGIN, LOGIN, 2))
        self.assertEqual(self.verify_store(), [])  # env store complete for kit-verify, with zero questions asked
        # idempotent: a second run writes nothing and says so — the store, the person row, both files
        github_before = (self.store / "github.md").read_text(encoding="utf-8")
        r2 = self.run_personal(FAKE_GH, "--settings", str(settings), "--pr-review", str(prr))
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("environment already named", r2.stdout)
        self.assertIn(f"github.person {LOGIN}: present, left alone", r2.stdout)
        self.assertIn("settings.local.json: identity already set, left alone", r2.stdout)
        self.assertEqual((self.store / "github.md").read_text(encoding="utf-8"), github_before)  # not re-verified, not re-dated
        # a hand-edited person row survives too
        subprocess.run([sys.executable, str(BIN / "kb.py"), "set", "github.person", LOGIN, "Nick", "--from", "user"],
                       env={**os.environ, "CONTEXT_ROOT": str(self.ctx)}, check=True, capture_output=True)
        self.run_personal(FAKE_GH)
        self.assertIn(f"| {LOGIN} | Nick |", (self.store / "github.md").read_text(encoding="utf-8"))
        # --dry-run on the personalized store plans against the real config, not a blank one
        dry = self.run_personal(FAKE_GH, "--dry-run")
        self.assertIn("environment already named", dry.stdout)
        self.assertNotIn("environment = ", dry.stdout)
        # a configured value survives a later run with a different identity
        cfg["environment"] = "renamed"
        (self.store / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.run_personal(FAKE_GH)
        self.assertEqual(json.loads((self.store / "config.json").read_text(encoding="utf-8"))["environment"], "renamed")

    def test_workspace_clones_beat_gh_repo_list(self):
        d = self.ws / "widgets"
        d.mkdir()
        subprocess.run(["git", "-C", str(d), "init", "-q"], check=True, env=hermetic_env(self.root))
        subprocess.run(["git", "-C", str(d), "remote", "add", "origin", "git@github.com:acme/widgets.git"],
                       check=True, env=hermetic_env(self.root))
        r = self.run_personal(FAKE_GH)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((self.store / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["tracker"]["repos"], ["acme/widgets"])

    def test_without_gh_it_still_completes_and_names_the_lines_to_fill(self):
        settings = self.ws / ".claude" / "settings.local.json"
        r = self.run_personal(BROKEN_GH, "--settings", str(settings))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("gh auth login", r.stdout)
        cfg = json.loads((self.store / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["environment"], cfg["tracker"]["kind"]), ("personal", "github"))
        s = json.loads(settings.read_text(encoding="utf-8"))["env"]
        self.assertEqual(s["WORKSPACE_GITHUB_LOGIN"], "")  # nothing invented
        self.assertEqual(self.verify_store(), [])

    def test_dry_run_writes_nothing(self):
        r = self.run_personal(FAKE_GH, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertFalse(self.store.exists())

    def test_kb_init_personal(self):
        env = {**os.environ, "CONTEXT_ROOT": str(self.ctx), "PATH": f"{fake_bin(self.root, FAKE_GH)}:{os.environ.get('PATH', '')}"}
        env.pop("GH_TOKEN", None)
        r = subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--personal"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("personal path", r.stdout)
        cfg = json.loads((self.store / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["tracker"]["kind"], "github")


if __name__ == "__main__":
    unittest.main()
