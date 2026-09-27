#!/usr/bin/env python3
"""personal.py — the zero-config personal path: a GitHub-only machine becomes a working environment in one
command and zero questions.

    python3 $BATON/context-db/bin/personal.py [--workspace DIR] [--settings FILE] [--pr-review FILE] [--dry-run]
    python3 $BATON/context-db/bin/kb.py init --personal        # the store part alone

Everything is DISCOVERED, nothing asked: identity from `gh api user` (login, display name), the tracked repos from the
clones under the workspace root (their `origin` remotes; `gh repo list` when there are none), the timezone from the OS.
A store whose `environment` is already named is left untouched (a blank one is filled), a filled `settings.local.json`
or a set pr-review `login` is never overwritten, and the one fact row is written only when absent — so the command is
idempotent and safe on an existing machine. Every
`systems.*` flag stays false: a skill that needs Jira, Slack or a warehouse stops with one "not applicable" line here,
and a corporate machine keeps the full path (`docs/new-environment.md`). What no tool settles (no `gh`, not logged in)
is reported as a line to fill, never a prompt. Provenance of the one fact row it writes: `tool:gh`.

Stdlib only; prints no token. `setup.sh --personal` runs it after the store exists."""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb  # noqa: E402
import kit_profile  # noqa: E402

GITHUB_TRACKER = {  # the derive facts of discovery/tracker-github.json, rendered once — nothing to discover
    "key_regex": r"(?:^|[^\w/])#(\d+)\b",
    "url_template": "https://github.com/{repo}/issues/{key}",
    "close_reasons": {"done": "completed", "wont_do": "not planned", "cancelled": "not planned"},
}
REMOTE = re.compile(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
PLACEHOLDER = re.compile(r"^\s*(<.*>)?\s*$")  # "" or "<your-github-login>": not a value


def run(cmd: list[str], timeout: int = 20) -> str | None:
    """stdout of a command that exited 0, else None (missing binary, non-zero, timeout) — never a traceback."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=kit_profile.gh_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def slug(s: str, fallback: str = "personal") -> str:
    """A lowercase environment slug (`[a-z][a-z0-9-]*`, what kit-verify accepts) from a login or a name."""
    s = re.sub(r"[^a-z0-9-]+", "-", (s or "").lower()).strip("-")
    if not s:
        return fallback
    return s if s[0].isalpha() else f"u-{s}"


def detect_tz(environ: dict | None = None, etc: Path = Path("/etc")) -> tuple[str, str]:
    """(IANA zone, source): $TZ, then /etc/timezone, then the /etc/localtime link, then timedatectl; UTC when nothing
    names a zone the zoneinfo database knows."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    environ = os.environ if environ is None else environ

    def valid(z: str) -> bool:
        try:
            ZoneInfo(z)
            return bool(re.fullmatch(r"[A-Za-z][\w+-]*(?:/[\w+-]+)*", z))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return False

    cands: list[tuple[str, str]] = [(environ.get("TZ", "").strip().lstrip(":"), "$TZ")]
    try:
        cands.append(((etc / "timezone").read_text(encoding="utf-8").strip(), "/etc/timezone"))
    except OSError:
        pass
    try:
        link = os.readlink(etc / "localtime")
        cands.append((link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else "", "/etc/localtime"))
    except OSError:
        pass
    cands.append((run(["timedatectl", "show", "-p", "Timezone", "--value"], timeout=5) or "", "timedatectl"))
    for z, src in cands:
        if z and valid(z):
            return z, src
    return "UTC", "default"


def gh_user() -> dict | None:
    """{login, name} from `gh api user`, None when gh is missing, not logged in or offline."""
    out = run(["gh", "api", "user", "--jq", "{login: .login, name: (.name // \"\")}"])
    if not out:
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) and d.get("login") else None


def workspace_repos(root: Path) -> list[str]:
    """`owner/repo` of every clone directly under the workspace root, from its `origin` remote (the kit's own clone
    excluded — it is not a repo this environment works on)."""
    out: set[str] = set()
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and p.name != ".claude" and not p.name.startswith("."))
    except OSError:
        return []
    for d in dirs:
        if not (d / ".git").exists():
            continue
        url = run(["git", "-C", str(d), "remote", "get-url", "origin"], timeout=5)
        m = REMOTE.search(url) if url else None
        if m:
            out.add(m.group(1))
    return sorted(out)


def gh_repos(limit: int = 50) -> list[str]:
    out = run(["gh", "repo", "list", "--limit", str(limit), "--json", "nameWithOwner", "--jq", ".[].nameWithOwner"], timeout=60)
    return sorted(set(out.split())) if out else []


def is_blank_store(cfg: dict) -> bool:
    """True while `environment` is still the blank default — the only signal that nothing was configured by hand."""
    return (cfg.get("environment") or kb.blank_config()["environment"]) == kb.blank_config()["environment"]


def personalize(cfg: dict, *, login: str, repos: list[str], tz: str) -> list[str]:
    """Fill the blank defaults of `cfg` in place for a GitHub-only machine; returns one line per key set. A store whose
    `environment` is already named is a configured machine: nothing is touched (a `tracker.kind: none` or `tz_default:
    UTC` chosen there is a choice, not a blank) — the caller says so."""
    blank = kb.blank_config()
    done: list[str] = []
    if not is_blank_store(cfg):
        return done

    def put(path: str, value, source: str, default) -> None:
        cur: dict = cfg
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        if cur.get(parts[-1], default) == default:
            cur[parts[-1]] = value
            done.append(f"{path} = {json.dumps(value) if not isinstance(value, str) else value}  ({source})")

    if login:
        put("environment", slug(login), "gh api user", blank["environment"])
        put("github.org", login, "gh api user (a personal machine: repos live under the user)", "")
    else:
        put("environment", "personal", "no gh login — rename with `kb.py config-set environment <name>`", blank["environment"])
    tracker = cfg.setdefault("tracker", {})
    if tracker.get("kind", "none") in ("none", ""):
        tracker["kind"] = "github"
        done.append("tracker.kind = github  (the only tracker a GitHub-only machine has)")
        for k, v in GITHUB_TRACKER.items():
            if not tracker.get(k):
                tracker[k] = v
                done.append(f"tracker.{k} = {json.dumps(v)}  (derived, discovery/tracker-github.json)")
        if not tracker.get("repos") and repos:
            tracker["repos"] = repos
            done.append(f"tracker.repos = {json.dumps(repos)}  (origin remotes of the workspace clones / gh repo list)")
    put("tz_default", tz, "the OS timezone", blank["tz_default"])
    return done


def apply_store(workspace: Path, dry_run: bool = False) -> tuple[list[str], dict]:
    """Create the store if needed and personalize it. Returns (report lines, the facts found) — the facts are reused
    for the identity files."""
    user = gh_user() or {}
    login, name = str(user.get("login") or ""), str(user.get("name") or "")
    repos = workspace_repos(workspace) or (gh_repos() if login else [])
    tz, tz_src = detect_tz()
    lines: list[str] = []
    if not dry_run:
        made = kb.init_blank()
        if made:
            lines.append("store created: " + ", ".join(made))
    cfg = kb.load_config() if kb.config_path().is_file() else kb.blank_config()  # reading never writes: dry run plans against the real store
    if is_blank_store(cfg):
        changed = personalize(cfg, login=login, repos=repos, tz=tz)
        lines += changed
        if not dry_run and changed:
            kb.migrate_config(cfg)
            kb.save_config(cfg)
    else:
        lines.append(f"config.json: environment already named ({cfg.get('environment')}) — left alone; a configured store is never "
                     f"re-filled (set `environment` back to {kb.blank_config()['environment']!r} to run the personal fill again)")
    if login and name:
        first = name.split()[0]
        present = kb.parse_doc("github")[1].get("person", {}) if kb.doc_path("github").is_file() else {}
        if login in present:
            lines.append(f"github.person {login}: present, left alone")
        elif dry_run:
            lines.append(f"github.person {login} = {first}  (gh api user, would add)")
        else:
            r = kb.set_fact("github", "person", login, first, "display name (you)", f"tool:gh {kb.today()}")
            lines.append(f"github.person {login} = {first}  (gh api user, {r})")
    if not login:
        lines.append("identity: `gh api user` gave no answer (gh missing, not logged in or offline) — run `gh auth login` "
                     "and re-run, or fill WORKSPACE_USER / WORKSPACE_GITHUB_LOGIN in .claude/settings.local.json by hand")
    lines.append(f"tz_default: {tz} ({tz_src})")
    return lines, {"login": login, "name": name, "repos": repos, "tz": tz}


def _read_json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _is_blank(v) -> bool:
    return not isinstance(v, str) or bool(PLACEHOLDER.match(v))


def apply_settings(path: Path, example: Path, facts: dict, dry_run: bool = False) -> list[str]:
    """Fill the identity `env` block of settings.local.json where it is still empty or a `<placeholder>`."""
    src = path if path.is_file() else example
    d = _read_json(src)
    if d is None:
        return [f"{path.name}: not valid JSON — left alone"]
    env = d.setdefault("env", {})
    want = {"WORKSPACE_USER": facts.get("name") or "", "WORKSPACE_GITHUB_LOGIN": facts.get("login") or "",
            "WORKSPACE_TZ": facts.get("tz") or ""}
    lines: list[str] = []
    for k, v in want.items():
        if v and _is_blank(env.get(k, "")):
            env[k] = v
            lines.append(f"{k} = {v}")
    for k in ("WORKSPACE_SLACK_SELF_DM", "WORKSPACE_SLACK_LATTICE_DM"):
        if _is_blank(env.get(k, "")):
            env[k] = ""  # no Slack on this path: an empty value, not a placeholder that reads as a value
    if not lines and path.is_file():
        return [f"{path.name}: identity already set, left alone"]
    if not dry_run:
        path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    return [f"{path.name}: " + (", ".join(lines) if lines else "created from the example")]


def apply_pr_review(path: Path, example: Path, facts: dict, dry_run: bool = False) -> list[str]:
    """pr-review config.json: `login`, `owner` and `sweep_repos` from the facts where they are still placeholders."""
    src = path if path.is_file() else example
    d = _read_json(src)
    if d is None:
        return [f"{path.name}: not valid JSON — left alone"]
    lines: list[str] = []
    login = facts.get("login") or ""
    if login and _is_blank(d.get("login")):
        d["login"] = login
        lines.append(f"login = {login}")
    if login and _is_blank(d.get("owner")):
        d["owner"] = login
        lines.append(f"owner = {login}")
    sweep = d.get("sweep_repos")
    if facts.get("repos") and (not isinstance(sweep, list) or all(_is_blank(s) for s in sweep)):
        d["sweep_repos"] = list(facts["repos"])
        lines.append(f"sweep_repos = {len(facts['repos'])} repo(s)")
    if not lines and path.is_file():
        return [f"{path.name}: already set, left alone"]
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    return [f"{path.name}: " + (", ".join(lines) if lines else "created from the example")]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="zero-config personal path: discover, never ask")
    ap.add_argument("--workspace", type=Path, default=kb.ROOT, help="the workspace root whose clones are the tracked repos (default: beside .claude/)")
    ap.add_argument("--settings", type=Path, help=".claude/settings.local.json to fill (created from the example when absent)")
    ap.add_argument("--pr-review", type=Path, help=".context/state/pr-review/config.json to fill (created from the example when absent)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be set, write nothing")
    a = ap.parse_args(argv[1:])
    lines, facts = apply_store(a.workspace, a.dry_run)
    if a.settings:
        lines += apply_settings(a.settings, kb.KIT / "settings.local.example.json", facts, a.dry_run)
    if a.pr_review:
        lines += apply_pr_review(a.pr_review, kb.KIT / "pr-review" / "config.example.json", facts, a.dry_run)
    head = "personal path (dry run — nothing written)" if a.dry_run else "personal path"
    print(f"{head}: {kb.ENV}")
    for ln in lines:
        print(f"  {ln}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
