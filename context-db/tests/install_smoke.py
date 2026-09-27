#!/usr/bin/env python3
"""install_smoke.py <clone|plugin> — replay README.md's install block for one path on a machine that has never seen
the kit, and check the workspace it leaves (#98). The block is read from README.md itself, so the test cannot drift
from the docs; only what a runner cannot do is swapped, each swap printed:

- the clone URL / marketplace source → this checkout (the change under test, not the published kit);
- `… && claude` (start a session) → dropped: there is no interactive session in CI;
- `/kit-setup` → the command in skills/kit-setup/SKILL.md § 2, with the env the plugin's SessionStart hook exports
  (`BATON`, `CLAUDE_PROJECT_DIR`);
- `/kit-health` → `kit-health.py --quiet` from the installed kit; RED (exit 2) fails, AMBER is reported.

Runs in a scratch HOME with `~/Projects` holding two repos and a stub `gh` (a fixed login, no network). The plugin
path needs the `claude` CLI (`npm i -g @anthropic-ai/claude-code`); without it that path is skipped (exit 0, one
line), so a laptop can run the clone path alone. Exit 0 = every check passed. Stdlib only.

A fresh install is AMBER, not GREEN: kit-health flags the stub's login and name (this scratch machine's own identity,
matched in this file) and "never stamped"; anything else is what a newcomer's first run would see, printed per line."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
LOGIN = "smoke-user"
GH_STUB = f"""#!/bin/sh
case "$*" in
  "api user"*) echo '{{"login":"{LOGIN}","name":"Smoke Test"}}' ;;
  *) exit 0 ;;
esac
"""


def readme_block(path: str) -> list[str]:
    """The fenced `sh` block under README.md's `**Plugin**` / `**Clone**` paragraph."""
    text = (KIT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^\*\*" + path.capitalize() + r"\*\*.*?\n```sh\n(.*?)\n```", text, re.S | re.M)
    if not m:
        sys.exit(f"install_smoke: no ```sh block under **{path.capitalize()}** in README.md")
    return m.group(1).splitlines()


def kit_setup_command() -> str:
    """The one command skills/kit-setup/SKILL.md § 2 runs."""
    text = (KIT / "skills" / "kit-setup" / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"## 2\. Run\n\n```sh\n(.*?)\n```", text, re.S)
    if not m:
        sys.exit("install_smoke: no ```sh block in skills/kit-setup/SKILL.md § 2")
    return m.group(1).strip()


def translate(path: str, lines: list[str]) -> tuple[list[str], list[str]]:
    """README lines → runnable shell lines, plus the swaps made (printed, so a reader sees what was not run as-is)."""
    out, swaps = [], []
    for line in lines:
        cmd = line.split("#", 1)[0].rstrip() if not line.lstrip().startswith("/") else line.split("#", 1)[0].strip()
        if not cmd:
            continue
        if cmd == "/kit-setup":
            cmd = 'BATON="$(installed_kit)"; CLAUDE_PROJECT_DIR="$PWD"; export BATON CLAUDE_PROJECT_DIR; ' + kit_setup_command()
            swaps.append("/kit-setup → skills/kit-setup § 2 with the hook's BATON / CLAUDE_PROJECT_DIR")
        elif cmd == "/kit-health":
            cmd = 'kit_health'
            swaps.append("/kit-health → kit-health.py --quiet (RED fails)")
        elif cmd.startswith("/"):
            sys.exit(f"install_smoke: README runs a slash command this test cannot replay: {cmd}")
        if "https://github.com/MdaaaaO/ai-baton.git" in cmd:
            cmd = cmd.replace("https://github.com/MdaaaaO/ai-baton.git", '"$KIT_SRC"')
            swaps.append("clone URL → this checkout")
        if "claude plugin marketplace add MdaaaaO/ai-baton" in cmd:
            cmd = cmd.replace("claude plugin marketplace add MdaaaaO/ai-baton", 'claude plugin marketplace add "$KIT_SRC"')
            swaps.append("marketplace source → this checkout")
        if re.search(r"&&\s*claude\s*$", cmd):
            cmd = re.sub(r"\s*&&\s*claude\s*$", "", cmd)
            swaps.append("`&& claude` (start a session) → dropped")
        out.append(cmd)
    return out, swaps


PRELUDE = r'''set -eu
installed_kit() {  # the plugin root Claude Code installed, or the clone
  if [ -d "$PWD/.claude/.git" ]; then echo "$PWD/.claude"; return; fi
  claude plugin list --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d if isinstance(d,list) else [dict(e,id=k) for k,v in d.get("plugins",{}).items() for e in v]; print(next(p["installPath"] for p in d if p["id"]=="ai-baton@ai-baton-kit"))'
}
kit_health() {
  rep="$(mktemp)"; set +e; python3 "$(installed_kit)/skills/kit-health/kit-health.py" --quiet --report "$rep"; rc=$?; set -e
  grep -E "^- (⚠️|❌)" "$rep" | cut -c1-200 || true
  echo "kit-health exit $rc"; [ "$rc" -lt 2 ]
}
'''


def main(argv: list[str]) -> int:
    require_cli = "--require-cli" in argv[2:]  # `make ci` and CI: a missing CLI is a failure, never a silent pass
    args = [x for x in argv[1:] if x != "--require-cli"]
    if len(args) != 1 or args[0] not in ("clone", "plugin"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    path = args[0]
    if path == "plugin" and not shutil.which("claude"):
        if require_cli:
            print("install_smoke plugin: FAILED — the `claude` CLI is not installed (npm i -g @anthropic-ai/claude-code)")
            return 1
        print("install_smoke plugin: skipped — the `claude` CLI is not installed (npm i -g @anthropic-ai/claude-code)")
        return 0
    work = Path(tempfile.mkdtemp(prefix=f"kit-install-smoke-{path}."))
    try:
        home, ws, stub = work / "home", work / "home" / "Projects", work / "bin"
        stub.mkdir(parents=True)
        (stub / "gh").write_text(GH_STUB)
        (stub / "gh").chmod(0o755)
        for repo in ("smoke-repo-one", "smoke-repo-two"):  # clones of the user's repos, as --personal expects to find them
            subprocess.run(["git", "init", "-q", str(ws / repo)], check=True)
            subprocess.run(["git", "-C", str(ws / repo), "remote", "add", "origin", f"https://github.com/{LOGIN}/{repo}.git"], check=True)
        # the kit under test as a clean git source (committed HEAD, as CI checks it out)
        src = work / "kit-src"
        subprocess.run(["git", "clone", "-q", str(KIT), str(src)], check=True)
        subprocess.run(["git", "-C", str(src), "checkout", "-q", "-B", "main"], check=True)  # a clone lands on main
        lines, swaps = translate(path, readme_block(path))
        print(f"== install smoke: {path} path, README block replayed ({len(lines)} commands)")
        for s in swaps:
            print(f"   swap: {s}")
        script = PRELUDE + "\n".join(lines) + "\n"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE_", "WORKSPACE_", "BATON", "CONTEXT_ROOT", "GH_", "GITHUB_TOKEN"))}
        env.update(HOME=str(home), PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", KIT_SRC=str(src),
                   GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t")
        r = subprocess.run(["bash", "-c", script], cwd=home, env=env, capture_output=True, text=True, timeout=600)
        print("\n".join("   | " + l for l in (r.stdout + r.stderr).rstrip().splitlines()[-40:]))
        fails = []
        if r.returncode != 0:
            fails.append(f"the README block exited {r.returncode}")
        cfg = ws / ".context" / "reference" / "env" / "config.json"
        if not cfg.is_file():
            fails.append(f"no env store at {cfg}")
        else:
            c = json.loads(cfg.read_text())
            if (c.get("kit") or {}).get("install_mode") != path:
                fails.append(f"kit.install_mode = {(c.get('kit') or {}).get('install_mode')!r}, want {path!r}")
            if (c.get("tracker") or {}).get("kind") != "github":
                fails.append("tracker.kind is not github (--personal did not fill the store)")
        prc = ws / ".context" / "state" / "pr-review" / "config.json"
        want = [f"{LOGIN}/smoke-repo-one", f"{LOGIN}/smoke-repo-two"]
        if not prc.is_file() or sorted(json.loads(prc.read_text()).get("sweep_repos") or []) != want:
            fails.append(f"pr-review sweep_repos is not {want}")
        for f in (ws / "CLAUDE.md", ws / ".context" / "README.md", ws / ".context" / "INDEX.md"):
            if not f.is_file():
                fails.append(f"missing {f.relative_to(ws)}")
        if path == "clone" and not (ws / ".claude" / "WORKSPACE.md").is_file():
            fails.append("no .claude/WORKSPACE.md in the clone")
        for f in fails:
            print(f"   FAIL {f}")
        print(f"== install smoke: {path}: {'FAIL' if fails else 'OK'}")
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
