"""bump_marketplace_ref.py: rewrites `.claude-plugin/marketplace.json`'s pinned github source `ref` to
`v<plugin.json version>` — the field conventional-release's `version-files` can't reach (a nested
`source.ref`, tag-prefixed, not a top-level `"version"` string). Run: make -C .claude/context-db test."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import bump_marketplace_ref as bmr  # noqa: E402


class Bump(unittest.TestCase):
    def write(self, root: Path, version="1.2.3", name="my-kit", ref="v1.2.2", source_type="github"):
        (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "version": version}), encoding="utf-8")
        source = {"source": source_type, "repo": "o/my-kit", "ref": ref} if source_type else "./"
        (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"name": "m", "owner": {"name": "o"}, "plugins": [{"name": name, "source": source}]},
            indent=2), encoding="utf-8")

    def test_rewrites_the_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, version="1.2.3", ref="v1.2.2")
            self.assertEqual(bmr.bump(root), "ref -> v1.2.3")
            market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
            self.assertEqual(market["plugins"][0]["source"]["ref"], "v1.2.3")
            # every other field survives untouched
            self.assertEqual(market["plugins"][0]["source"]["repo"], "o/my-kit")

    def test_idempotent_when_already_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, version="1.2.3", ref="v1.2.3")
            before = (root / ".claude-plugin" / "marketplace.json").read_text()
            self.assertEqual(bmr.bump(root), "unchanged")
            self.assertEqual((root / ".claude-plugin" / "marketplace.json").read_text(), before)

    def test_refuses_a_non_github_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, source_type=None)  # the old "./" relative-path source
            with self.assertRaises(SystemExit) as cm:
                bmr.bump(root)
            self.assertIn("not a pinned github source", str(cm.exception))

    def test_refuses_no_matching_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, name="my-kit")
            (root / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "other-kit", "version": "1.2.3"}), encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                bmr.bump(root)
            self.assertIn("no plugins[] entry named", str(cm.exception))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, version="1.2.3", ref="v1.2.2")
            self.assertEqual(bmr.main(["--root", str(root)]), 0)
            self.write(root, source_type=None)
            self.assertEqual(bmr.main(["--root", str(root)]), 1)


if __name__ == "__main__":
    unittest.main()
