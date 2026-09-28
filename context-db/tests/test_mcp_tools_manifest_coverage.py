"""Every `tracker.mcp_tools.<name>` a skill or agent body names must have a discovery manifest entry —
otherwise `env-init` can't fill it on a Jira machine and kit-health's fact coverage never sees it. This
caught #180's `edit` / `transitions_list` / `remote_link`, added to the config template and routed
through in prose but never given a manifest row. Stdlib unittest, no store needed (reads the kit's own
files). Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
DISCOVERY = KIT / "context-db" / "discovery"

MCP_TOOL_REF = re.compile(r"tracker\.mcp_tools\.([A-Za-z_][A-Za-z0-9_]*)")


def referenced_mcp_tool_keys() -> dict[str, list[str]]:
    """Every distinct `tracker.mcp_tools.<name>` mentioned in a skill/agent body → the file(s) it appears in."""
    out: dict[str, list[str]] = {}
    for p in sorted((KIT / "skills").glob("*/SKILL.md")) + sorted((KIT / "agents").glob("*.md")):
        text = p.read_text(encoding="utf-8")
        rel = str(p.relative_to(KIT))
        for m in MCP_TOOL_REF.finditer(text):
            key = f"tracker.mcp_tools.{m.group(1)}"
            out.setdefault(key, [])
            if rel not in out[key]:
                out[key].append(rel)
    return out


def manifest_mcp_tool_keys() -> set[str]:
    """Every `tracker.mcp_tools.<name>` a discovery manifest actually covers."""
    keys: set[str] = set()
    for p in DISCOVERY.glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        for f in data.get("facts", []):
            if isinstance(f, dict) and isinstance(f.get("key"), str) and f["key"].startswith("tracker.mcp_tools."):
                keys.add(f["key"])
    return keys


class McpToolsManifestCoverage(unittest.TestCase):
    def test_every_referenced_mcp_tool_key_has_a_manifest_entry(self):
        referenced = referenced_mcp_tool_keys()
        covered = manifest_mcp_tool_keys()
        missing = {key: files for key, files in referenced.items() if key not in covered}
        self.assertEqual(missing, {}, f"referenced but not in any context-db/discovery/*.json manifest: {missing}")

    def test_the_scan_actually_finds_something(self):
        # a regex that stopped matching (a rename, a typo) would make the coverage test vacuously pass
        self.assertGreaterEqual(len(referenced_mcp_tool_keys()), 5)


if __name__ == "__main__":
    unittest.main()
