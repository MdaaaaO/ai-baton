"""mission.py — the mission-control page build: data.json, build.json, index.html, last.sha and a self-written .gitignore.

Every fixture is a copy of one placeholder store (a blank env store, environment "ci", filled with sessions, a
ledger, 40 docs, queues and pointers) in a temp dir. mission.py runs against it as a subprocess, because the engine
modules fix their content root when they are imported and this suite's own import is pinned to another throw-away
store; the pure helpers (is_stale, number, issue_url, open_prs, parse_local, script_safe, render) are called in
process. Nothing here touches a real store, and every value is a placeholder. Stdlib unittest.
Run: make -C $BATON/context-db test T=test_mission."""
from __future__ import annotations
import ast
import contextlib
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENGINE = Path(__file__).resolve().parents[1]
KIT = ENGINE.parent
BIN = ENGINE / "bin"
MISSION = BIN / "mission.py"
sys.path.insert(0, str(BIN))
from tests import hermetic_env  # noqa: E402
import gen_sessions  # noqa: E402 — same dir: the registry parser mission.py must agree with
import kit_profile as profile  # noqa: E402 — same dir
import mission  # noqa: E402 — same dir: the pure helpers are called in process
import session  # noqa: E402 — same dir: the ledger header the fixture ledger is written with
import session_stats  # noqa: E402 — same dir: the one spend-basis wording

ZONE_NAME = "Etc/GMT-2"  # a fixed +02 offset without DST: 2026-10-10T12:00:00Z reads 2026-10-10 02:00 PM +02
ZONE = ZoneInfo(ZONE_NAME)
AS_OF = "2026-10-10T12:00:00Z"  # the newest timestamp the fixture plants (kit-alpha's heartbeat)
TRACKER_TEMPLATE = "https://tracker.example/browse/{key}"
TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1"  # built at run time: no secret-shaped literal in the source
EMAIL = "someone" + "@" + "example.invalid"
SESSION_INDEX_STAMP = "2026-10-10T09:30:00Z"
INDEX_DATE = "2026-10-10"
TOP_KEYS = ["as_of", "catalog", "cost", "links", "pointers", "priorities", "queues", "schema", "sessions",
            "stale_hours", "tz"]
OUTPUTS = ["data.json", "build.json", "index.html", "last.sha", ".gitignore"]

# a tiny page: every token the engine knows, one part, both script blocks
TINY = ('<!doctype html>\n<title>t</title>\n<p>kit {{KIT_VERSION}}</p>\n{{part:x.html}}\n'
        '<script id="data" type="application/json">{{DATA_JSON}}</script>\n'
        '<script id="build" type="application/json">{{BUILD_JSON}}</script>\n')
PART_X = "<i>part-x</i>\n"

# (ended UTC, session, epic, turns, hours, ctx peak, cache-read, out, (total, main, subagents), compactions, PRs,
# tickets, sign jobs, drafts) — the shape session.py's ledger writer appends; `-` is "no epic"
LEDGER_ROWS = [
    ("2026-10-09T10:00:00Z", "kit-alpha", "KEY-123", 100, 2.5, "345k", "1.2M", "812", (40, 30, 10), 2, 3, 1, 0, 0),
    ("2026-10-08T10:00:00Z", "kit-beta", "acme/widgets#42", 80, 2, "210k", "900k", "640", (32, 24, 8), 1, 2, 2, 0, 0),
    ("2026-10-06T10:00:00Z", "kit-alpha", "KEY-123", 60, 1.5, "180k", "700k", "500", (30, 20, 10), 1, 1, 0, 0, 0),
    ("2026-10-04T10:00:00Z", "kit-gamma", "-", 40, 1, "120k", "400k", "300", (20, 20, 0), 0, 0, 0, 1, 0),
    ("2026-10-02T10:00:00Z", "kit-alpha", "KEY-123", 30, 1, "100k", "300k", "250", (15, 10, 5), 0, 1, 0, 0, 0),
    ("2026-09-28T10:00:00Z", "kit-beta", "acme/widgets#42", 25, 1, "90k", "200k", "200", (14, 14, 0), 0, 0, 1, 0, 0),
    ("2026-09-20T10:00:00Z", "kit-delta", "KEY-456", 20, 0.5, "80k", "150k", "150", (13, 9, 4), 0, 1, 0, 0, 0),
    ("2026-09-12T10:00:00Z", "kit-delta", "KEY-456", 18, 0.5, "70k", "120k", "120", (13, 13, 0), 0, 0, 0, 0, 0),
    ("2026-09-05T10:00:00Z", "kit-zeta", "-", 15, 0.5, "60k", "100k", "100", (30, 30, 0), 0, 0, 0, 0, 0),  # raw UTC cell
    ("long ago", "kit-zeta", "-", 10, 0.5, "50k", "80k", "90", (25, 25, 0), 0, 0, 0, 0, 0),  # not a time at all
]
LEDGER_USD = 232  # the sum of the ten `~$` cells above
LOCALIZED_ROWS = 8  # the first rows carry the display zone's rendering of the end time, as the writer writes it

DOMAINS = (("repos", 14), ("meetings", 13), ("notes", 13))  # 40 docs
STATUSES = ("active", "reference", "draft", "done")
TYPES = ("reference", "log", "note")
SSH_TITLE = "Remote git@github.com:acme/x.git notes"  # an SSH remote in a title: not an e-mail address
NEXT_PROMPT = "Register as kit-delta-2, read the open PR list, then continue the export."  # kit-delta's hand-off


def engine_env(root, **extra) -> dict:
    """The environment of an engine subprocess: this suite's (WORKSPACE_* and git variables already dropped by the
    tests package) with CONTEXT_ROOT pointing at the fixture, no TZ, and UTF-8 pipes."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_") and k != "TZ"}
    env.update(CONTEXT_ROOT=str(root), PYTHONIOENCODING="utf-8")
    env.update(extra)
    return env


def run_tool(script: Path, root, *args, env=None, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", timeout=120, env=env if env is not None else engine_env(root),
                          cwd=str(cwd if cwd else Path(root).parent))


def run_mission(root, *args, script: Path = MISSION, env=None, cwd=None) -> subprocess.CompletedProcess:
    return run_tool(script, root, *args, env=env, cwd=cwd)


def write(root, rel: str, text: str) -> Path:
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def plant(root, rel: str, old: str, new: str) -> None:
    """Replace the first `old` in a fixture file — the test fails loudly when the fixture does not hold it."""
    path = Path(root) / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"plant: {old!r} is not in {rel}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def script_json(page: str, ident: str) -> str:
    m = re.search(rf'<script id="{ident}" type="application/json">(.*?)</script>', page, re.S)
    if not m:
        raise AssertionError(f"no <script id={ident!r}> block in the page")
    return m.group(1)


def unstamped(text: str) -> str:
    """The page without its build stamp: `generated_at` is the one value two builds of one store may disagree on."""
    return re.sub(r'"generated_at": "[^"]*"', '"generated_at": "-"', text)


def snapshot(directory) -> dict:
    """relative path -> (inode, mtime_ns, sha256): equal snapshots mean nothing was rewritten (an atomic write always
    gives the file a new inode), not merely that the bytes are the same."""
    out = {}
    for p in sorted(Path(directory).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(directory).as_posix()] = (st.st_ino, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def tree_digest(root, skip: str = "") -> dict:
    """relative path -> sha256 of every file under `root` except the directory `skip` (relative to root)."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not (skip and (rel == skip or rel.startswith(skip + "/"))):
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=hermetic_env(Path(repo).parent)).stdout.strip()


def sized(text: str, size: int) -> str:
    """`text` padded (inside its body) to exactly `size` bytes; the text must be ASCII."""
    body = text.rstrip("\n")
    return body + "x" * (size - len(body) - 1) + "\n"


# ── the fixture store ─────────────────────────────────────────────────────────────────────────────────────────
def session_doc(name: str, status: str, heartbeat: str, *, epic: str = "", repos: str = "acme/widgets",
                working_on: str = "plan the export", stats: str = "1 turns · 0.1h", prompt: str = "",
                prs: str = "none", crons: str = "") -> str:
    body = f"# Session: {name}\n\n## Next session\n\n{prompt}\n\n## Open PRs\n\n{prs}\n"
    if crons:
        body += f"\n## Crons\n\n{crons}\n"
    return (f"---\nsession: {name}\nref: abc123\nstatus: {status}\nepic: {epic}\nrepos: {repos}\n"
            f"working_on: {working_on}\nresponsibilities: owns the export\nstats: {stats}\n"
            f"heartbeat: {heartbeat}\nupdated: {heartbeat[:10]}\n---\n\n{body}")


def doc_text(title: str, dtype: str, domain: str, status: str, updated: str, body: str = "A placeholder note.") -> str:
    return (f"---\ntitle: {title}\ntype: {dtype}\ndomain: {domain}\ntags: [fixture, {domain}]\nstatus: {status}\n"
            f"updated: {updated}\n---\n\n# {title}\n\n{body}\n")


def ledger_text() -> str:
    lines = []
    for i, (ended, name, epic, turns, hours, ctx, cache, out, usd, comp, prs, tickets, sign, drafts) in enumerate(LEDGER_ROWS):
        cell = profile.local_str(ended, ZONE) if i < LOCALIZED_ROWS else ended
        total, main, sub = usd
        lines.append(f"| {cell} | `{name}` | {epic} | {turns} | {hours} | {ctx} | {cache} | {out} | "
                     f"{total:.0f} ({main:.0f}+{sub:.0f}) | {comp} | {prs} | {tickets} | {sign} | {drafts} |")
    return session.LEDGER_FRONTMATTER + session.LEDGER_HEADER + "\n".join(lines) + "\n"


def write_sessions(root: Path) -> None:
    write(root, "sessions/kit-alpha.md", session_doc(
        "kit-alpha", "active", AS_OF, epic="KEY-123", repos="acme/widgets, acme/gadgets",
        stats="100 turns · 2.5h · ctx peak 345k · ~$44 (30+14)"))
    write(root, "sessions/kit-beta.md", session_doc(
        "kit-beta", "active", "2026-10-10T10:00:00Z", epic="acme/widgets#42", working_on='"wire the export: step 2"',
        stats="20 turns · 0.6h · ~$3.5",
        prs="- acme/widgets#7 — draft, waiting on CI\n- widgets#8 follow-up\n- #9",
        crons="- `*/30 * * * *` poll the review queue\n- `0 9 * * 1-5` standup reminder"))
    write(root, "sessions/kit-gamma.md", session_doc(  # more than STALE_HOURS before every other heartbeat
        "kit-gamma", "active", "2026-10-09T06:00:00Z", stats="5 turns · 0.2h"))
    write(root, "sessions/kit-delta.md", session_doc(
        "kit-delta", "ended", "2026-10-09T18:00:00Z", epic="KEY-456",
        prompt=NEXT_PROMPT))
    write(root, "sessions/kit-epsilon.md", session_doc("kit-epsilon", "ended", "2026-10-09T20:00:00Z"))
    write(root, "sessions/archive/kit-zeta.md", session_doc("kit-zeta", "ended", "2026-09-20T10:00:00Z"))
    write(root, "sessions/_ledger.md", ledger_text())


def write_docs(root: Path) -> None:
    index = 0
    for domain, count in DOMAINS:
        for i in range(count):
            title = SSH_TITLE if (domain, i) == ("repos", 0) else f"{domain.title()} note {i:02d}"
            text = doc_text(title, TYPES[index % len(TYPES)], domain, STATUSES[index % len(STATUSES)],
                            f"2026-09-{1 + index % 28:02d}")
            if (domain, i) == ("notes", 0):
                text = sized(text, 31 * 1024)  # over the 30 KB mark
            elif (domain, i) == ("notes", 1):
                text = sized(text, 30 * 1024)  # exactly on it: not large
            write(root, f"{domain}/{domain}-{i:02d}.md", text)
            index += 1


def write_sources(root: Path) -> None:
    write(root, "reference/priorities.md", doc_text("Priorities", "reference", "reference", "active", "2026-10-09", (
        "- [ ] Orphan item before any heading\n\n## Now\n\n- [ ] Ship the widget exporter\n- [x] Close the audit ticket\n\n"
        "## Later\n\n- [ ] Tidy the backlog\n* [X] Archive the old notes\n\n```text\n- [ ] Not an item, it sits in a fence\n```")))
    write(root, "memory/MEMORY.md", (
        "# Memory index\n\n- [User identity](user-identity.md) — a placeholder person with a placeholder role\n"
        "- [Build habits](build-habits.md) — run the full CI target before pushing\n- [Plain pointer](plain.md)\n"))
    write(root, "kit-health/2026-10-08-walk.md", doc_text("Kit health walk 2026-10-08", "log", "kit-health", "done",
                                                          "2026-10-08", "## Outcome of the walk — 5 findings, 1 blocker\n\nOlder walk."))
    write(root, "kit-health/2026-10-09-walk.md", doc_text(
        "Kit health walk 2026-10-09", "log", "kit-health", "done", "2026-10-09",
        "Walked every skill.\n\n## Outcome of the walk — 2 findings, 0 blockers\n\nFiled two follow-ups; nothing blocks the release."))
    write(root, "kit-health/HEALTH-test.md", doc_text("Kit health stamp", "reference", "kit-health", "active", "2026-10-09"))
    rows = [{"repo": "acme/widgets", "pr": 7, "head": "abc1234", "status": "posted", "ts": "2026-10-07T09:00:00Z"},
            {"repo": "acme/widgets", "pr": "8", "head": "def5678", "status": "skipped", "ts": "2026-10-08T09:00:00Z"},
            {"repo": "widgets", "pr": 7, "head": "0123abc", "status": "posted", "ts": "2026-10-09T09:00:00Z"}]
    write(root, "state/pr-review/ledger.jsonl", "\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    (Path(root) / "state" / "sign-queue").mkdir(parents=True, exist_ok=True)


def build_base(tmp: Path) -> Path:
    """The pristine fixture store: `<tmp>/base/.context`."""
    root = tmp / "base" / ".context"
    root.mkdir(parents=True)

    def engine(script: str, *args: str) -> None:
        r = run_tool(BIN / script, root, *args)
        if r.returncode:
            raise RuntimeError(f"fixture: {script} {' '.join(args)} failed: {r.stderr.strip()}")

    engine("kb.py", "init", "--blank")
    for key, value in (("environment", "ci"), ("tz_default", ZONE_NAME), ("tracker.kind", "jira"),
                       ("tracker.url_template", TRACKER_TEMPLATE), ("github.org", "acme"),
                       ("tracker.repos", '["acme/widgets"]')):
        engine("kb.py", "config-set", key, value)
    for p in (root / "reference" / "env").glob("*.md"):  # the blank store stamps today's date: pin it
        p.write_text(re.sub(r"(?m)^updated: .*$", "updated: 2026-09-01", p.read_text(encoding="utf-8"), count=1),
                     encoding="utf-8")
    write_sessions(root)
    write_docs(root)
    write_sources(root)
    engine("gen_index.py")
    engine("gen_sessions.py", "--no-archive")
    plant_regex(root / "INDEX.md", r"(_\d+ docs · generated )\d{4}-\d{2}-\d{2}(_)", rf"\g<1>{INDEX_DATE}\g<2>")
    plant_regex(root / "SESSION_INDEX.md", r"^(_generated ).+?( · \d+ active)",
                lambda m: m.group(1) + profile.local_str(SESSION_INDEX_STAMP, ZONE) + m.group(2))
    return root


def plant_regex(path: Path, pattern: str, repl) -> None:
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, text, count=1, flags=re.M)
    if n != 1:
        raise RuntimeError(f"fixture: no generated line matching {pattern!r} in {path.name}")
    path.write_text(new, encoding="utf-8")


_TMP = None
BASE: Path
TPL: Path
BUILT: Path
DATA: dict


def setUpModule():
    global _TMP, BASE, TPL, BUILT, DATA
    _TMP = tempfile.TemporaryDirectory(prefix="kit-mission-")
    tmp = Path(_TMP.name)
    BASE = build_base(tmp)
    TPL = write(tmp, "tpl/tiny.tpl.html", TINY)
    write(tmp, "tpl/parts/x.html", PART_X)
    BUILT = tmp / "built"
    r = run_mission(BASE, "build", "--out", BUILT, "--template", TPL)
    if r.returncode:
        raise RuntimeError(f"fixture: the base build failed: {r.stdout}{r.stderr}")
    DATA = json.loads((BUILT / "data.json").read_text(encoding="utf-8"))


def tearDownModule():
    _TMP.cleanup()


# ── the tests ─────────────────────────────────────────────────────────────────────────────────────────────────
def kit_version() -> str:
    return (KIT / "VERSION").read_text(encoding="utf-8").strip()


def keys_of(node):
    """Every dict key anywhere under `node`."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from keys_of(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys_of(v)


class Store(unittest.TestCase):
    """A private copy of the pristine store per test, an output dir beside it, and the three commands under test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kit-mission-t-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / ".context"
        shutil.copytree(BASE, self.root)
        self.out = self.tmp / "out"

    def reset(self) -> None:
        """Back to the pristine store and an empty output dir (for a test that tries several plants in turn)."""
        shutil.rmtree(self.root)
        shutil.copytree(BASE, self.root)
        shutil.rmtree(self.out, ignore_errors=True)

    def mission(self, *args, **kw) -> subprocess.CompletedProcess:
        return run_mission(self.root, *args, **kw)

    def command(self, cmd: str, args: tuple, template) -> list:
        """`cmd --out OUT [--template T] *args`; the page template is the tiny fixture one unless told otherwise."""
        line = [cmd, "--out", self.out]
        if cmd != "open" and "--template" not in args and template is not None:
            line += ["--template", TPL if template is True else template]
        return line + list(args)

    def build(self, *args, template=True, **kw) -> subprocess.CompletedProcess:
        return self.mission(*self.command("build", args, template), **kw)

    def diff(self, *args, template=True, **kw) -> subprocess.CompletedProcess:
        return self.mission(*self.command("diff", args, template), **kw)

    def open_page(self, **kw) -> subprocess.CompletedProcess:
        return self.mission(*self.command("open", (), None), **kw)

    def git_env(self) -> dict:
        """The engine's env with a hermetic git around it (HOME and the git config are the fixture's)."""
        env = hermetic_env(self.tmp)
        env.update(CONTEXT_ROOT=str(self.root), PYTHONIOENCODING="utf-8")
        env.pop("TZ", None)
        return env

    def text(self, name: str) -> str:
        return (self.out / name).read_text(encoding="utf-8")

    def data(self) -> dict:
        return json.loads(self.text("data.json"))

    def build_json(self) -> dict:
        return json.loads(self.text("build.json"))


class BuildFiles(Store):
    def test_build_writes_the_five_files(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(OUTPUTS))
        self.assertEqual(r.stdout, f"mission: built {self.out / 'index.html'} {self.build_json()['page_sha'][:12]}\n")
        self.assertEqual(r.stderr, "")

    def test_the_gitignore_is_a_single_star(self):
        self.build()
        self.assertEqual(self.text(".gitignore"), "*\n")

    def test_an_existing_gitignore_is_left_alone(self):
        write(self.out, ".gitignore", "mine\n")
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.text(".gitignore"), "mine\n")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(OUTPUTS))

    def test_the_shas_and_last_sha(self):
        self.build()
        build = self.build_json()
        data_bytes = (self.out / "data.json").read_bytes()
        page = TINY.replace("{{part:x.html}}", PART_X).replace("{{KIT_VERSION}}", kit_version())
        self.assertEqual(build["data_sha"], hashlib.sha256(data_bytes).hexdigest())
        self.assertEqual(build["page_sha"], hashlib.sha256(data_bytes + page.encode("utf-8")).hexdigest())
        self.assertEqual(self.text("last.sha"), build["page_sha"] + "\n")

    def test_build_json_fields(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        self.build()
        build = self.build_json()
        self.assertEqual(sorted(build), ["data_sha", "generated_at", "kit_version", "page_sha", "store_sha", "template"])
        made = datetime.strptime(build["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(before, made)
        self.assertLess(made - before, timedelta(minutes=5))
        self.assertEqual(build["kit_version"], kit_version())
        self.assertIsNone(build["store_sha"])  # the fixture store is no git repository of its own
        self.assertEqual(build["template"], "tiny.tpl.html")

    def test_the_default_out_dir_is_state_mission_control(self):
        r = self.mission("build", "--template", TPL)
        default = self.root / "state" / "mission-control"
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in default.iterdir()), sorted(OUTPUTS))
        self.assertEqual(r.stdout.split()[2], str(default / "index.html"))
        self.assertEqual(self.mission("open").stdout, f"{default / 'index.html'}\n")

    def test_out_creates_nested_directories_and_may_be_relative(self):
        r = self.mission("build", "--out", "a/b/c", "--template", TPL, cwd=self.tmp)
        nested = self.tmp / "a" / "b" / "c"
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in nested.iterdir()), sorted(OUTPUTS))
        self.assertIn(str(nested / "index.html"), r.stdout)
        self.assertEqual(self.mission("open", "--out", "a/b/c", cwd=self.tmp).stdout, f"{nested / 'index.html'}\n")

    def test_a_second_build_is_unchanged_and_writes_nothing(self):
        self.assertEqual(self.build().returncode, 0)
        before = snapshot(self.out)
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"mission: unchanged {self.build_json()['page_sha'][:12]} {self.out}\n")
        self.assertEqual(snapshot(self.out), before)  # same inodes, mtimes and bytes: not one file was rewritten

    def test_force_rebuilds_the_same_page(self):
        self.build()
        before, page = snapshot(self.out), self.text("index.html")
        r = self.build("--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("mission: built "), r.stdout)
        after = snapshot(self.out)
        self.assertEqual(sorted(after), sorted(before))
        for name in ("data.json", "build.json", "index.html", "last.sha"):
            self.assertNotEqual(after[name][0], before[name][0], f"{name} was not rewritten")
        for name in ("data.json", "last.sha"):
            self.assertEqual(after[name][2], before[name][2], f"{name} changed bytes")
        self.assertEqual(unstamped(self.text("index.html")), unstamped(page))  # only the build stamp may differ
        self.assertEqual(after[".gitignore"], before[".gitignore"])  # it exists: left as it is

    def test_a_changed_store_rebuilds_with_a_new_page_sha(self):
        self.build()
        first = self.build_json()["page_sha"]
        plant(self.root, "sessions/kit-gamma.md", "working_on: plan the export", "working_on: draft the export")
        r = self.build()
        self.assertTrue(r.stdout.startswith("mission: built "), r.stdout)
        self.assertNotEqual(self.build_json()["page_sha"], first)
        self.assertEqual(self.text("last.sha"), self.build_json()["page_sha"] + "\n")
        self.assertIn("draft the export", self.text("data.json"))

    def test_a_missing_output_is_rebuilt_not_called_unchanged(self):
        self.build()
        for name in ("index.html", ".gitignore"):
            with self.subTest(missing=name):
                (self.out / name).unlink()
                r = self.build()
                self.assertTrue(r.stdout.startswith("mission: built "), r.stdout)
                self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(OUTPUTS))

    def test_diff_before_the_first_build(self):
        r = self.diff()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, f"mission: not built {self.out}\n", ""))
        self.assertFalse(self.out.exists())

    def test_diff_after_a_build_and_after_a_store_change(self):
        self.build()
        before = snapshot(self.out)
        r = self.diff()
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout, f"mission: unchanged {self.build_json()['page_sha'][:12]} {self.out}\n")
        plant(self.root, "sessions/kit-gamma.md", "working_on: plan the export", "working_on: draft the export")
        r = self.diff()
        self.assertEqual(r.returncode, 1)
        m = re.fullmatch(r"mission: changed ([0-9a-f]{12}) -> ([0-9a-f]{12}) " + re.escape(str(self.out)) + r"\n", r.stdout)
        self.assertTrue(m, r.stdout)
        self.assertEqual(m.group(1), self.build_json()["page_sha"][:12])
        self.assertNotEqual(m.group(1), m.group(2))
        self.assertEqual(snapshot(self.out), before)  # diff never writes
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.diff().returncode, 0)

    def test_diff_sees_a_template_change(self):
        self.build()
        other = write(self.tmp, "tpl2/other.tpl.html", TINY.replace("<title>t</title>", "<title>other</title>"))
        write(self.tmp, "tpl2/parts/x.html", PART_X)
        r = self.diff(template=other)
        self.assertEqual(r.returncode, 1)
        self.assertIn("mission: changed ", r.stdout)

    def test_diff_reports_an_incomplete_build(self):
        self.build()
        (self.out / "index.html").unlink()
        r = self.diff()
        self.assertEqual((r.returncode, r.stdout), (1, f"mission: incomplete {self.out}\n"))

    def test_diff_runs_no_gate(self):
        self.build()
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: token {TOKEN}")
        r = self.diff()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("mission: changed "), r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)

    def test_open_before_and_after_a_build(self):
        r = self.open_page()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertEqual(r.stderr, f"mission: nothing built at {self.out / 'index.html'} — run build first\n")
        self.build()
        r = self.open_page()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, f"{self.out / 'index.html'}\n", ""))
        self.assertTrue(os.path.isabs(r.stdout.strip()))

    def test_a_build_leaves_the_rest_of_the_store_alone(self):
        before = tree_digest(self.root, skip="state/mission-control")
        self.assertEqual(self.mission("build", "--template", TPL).returncode, 0)
        self.assertEqual(tree_digest(self.root, skip="state/mission-control"), before)

    def test_the_default_out_dir_stays_out_of_the_stores_own_git(self):
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "fixture")
        r = self.mission("build", "--template", TPL, env=self.git_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.root, "status", "--porcelain", "--ignored=no"), "")
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertTrue((self.root / "state" / "mission-control" / "index.html").is_file())


class DataShape(unittest.TestCase):
    def test_data_json_is_canonical_json(self):
        text = (BUILT / "data.json").read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(DATA, sort_keys=True, indent=1, ensure_ascii=False) + "\n")
        self.assertTrue(text.endswith("}\n"))
        self.assertIn("·", text)  # non-ASCII text stays as written, not \u-escaped

    def test_the_top_level_keys_and_scalars(self):
        self.assertEqual(sorted(DATA), TOP_KEYS)
        self.assertEqual(DATA["schema"], 1)
        self.assertEqual(DATA["tz"], ZONE_NAME)
        self.assertEqual(DATA["stale_hours"], gen_sessions.STALE_HOURS)
        self.assertEqual(DATA["as_of"], AS_OF)

    def test_no_clock_and_no_age_anywhere(self):
        names = set(keys_of(DATA))
        self.assertFalse(names & {"now", "generated_at", "age", "age_hours", "stale", "is_stale"}, names)

    def test_the_section_shapes(self):
        self.assertEqual(sorted(DATA["links"]), ["github_org", "repos", "tracker_kind", "tracker_url_template"])
        self.assertEqual(sorted(DATA["sessions"]), ["active", "archived", "counts", "ended", "problems"])
        self.assertEqual(sorted(DATA["cost"]), ["basis", "ledger", "live", "totals"])
        self.assertEqual(sorted(DATA["cost"]["totals"]), ["by_epic", "hours", "last_30d", "last_7d", "rows", "turns", "usd"])
        self.assertEqual(sorted(DATA["catalog"]), ["counts", "docs", "domains", "problems"])
        self.assertEqual(sorted(DATA["priorities"]), ["items", "path", "present"])
        self.assertEqual(sorted(DATA["queues"]), ["kit_health", "pr_review", "sign_queue"])
        self.assertEqual(sorted(DATA["pointers"]), ["index", "memory", "session_index", "sources"])
        entry = DATA["sessions"]["active"][0]
        self.assertEqual(sorted(entry), ["cost_usd", "crons", "epic", "epic_url", "has_prompt", "heartbeat",
                                         "heartbeat_local", "name", "open_prs", "path", "prompt", "ref", "repos",
                                         "responsibilities", "stats", "status", "working_on"])

    def test_every_url_is_a_web_url_or_null(self):
        def urls(node, key=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from urls(v, k)
            elif isinstance(node, list):
                for v in node:
                    yield from urls(v, key)
            elif key == "url" or key.endswith("_url"):
                yield node
        seen = list(urls(DATA))
        self.assertGreater(len(seen), 10)
        for value in seen:
            with self.subTest(url=value):
                self.assertTrue(value is None or re.fullmatch(r"https?://[^\s<>\"']+", value), value)
                self.assertNotIn("/home/", value or "")
        self.assertIn(None, seen)  # an unresolvable reference is null, never a guess

    def test_the_page_carries_exactly_the_data_that_was_written(self):
        page = (BUILT / "index.html").read_text(encoding="utf-8")
        self.assertEqual(json.loads(script_json(page, "data")), DATA)
        self.assertEqual(json.loads(script_json(page, "build")), json.loads((BUILT / "build.json").read_text(encoding="utf-8")))


class AsOf(Store):
    def test_as_of_is_the_newest_timestamp_of_any_source(self):
        queue = self.root / "state" / "sign-queue"
        newer = datetime(2026, 10, 12, 11, 0, 0, tzinfo=timezone.utc)

        def heartbeat():
            plant(self.root, "sessions/kit-gamma.md", "heartbeat: 2026-10-09T06:00:00Z", "heartbeat: 2026-10-12T08:00:00Z")

        def archived_heartbeat():
            plant(self.root, "sessions/archive/kit-zeta.md", "heartbeat: 2026-09-20T10:00:00Z", "heartbeat: 2026-10-12T08:30:00Z")

        def ledger_row():
            with open(self.root / "sessions" / "_ledger.md", "a", encoding="utf-8") as f:
                f.write("| 2026-10-12T09:00:00Z | `kit-x` | - | 1 | 0.1 | 1k | 1k | 1 | 1 (1+0) | 0 | 0 | 0 | 0 | 0 |\n")

        def pr_row():
            with open(self.root / "state" / "pr-review" / "ledger.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"repo": "acme/widgets", "pr": 9, "head": "abc", "status": "posted",
                                    "ts": "2026-10-12T10:00:00Z"}) + "\n")

        def sign_job():
            job = write(queue, "job.sh", "echo placeholder\n")
            os.utime(job, (newer.timestamp(), newer.timestamp()))

        def doc():
            plant(self.root, "repos/repos-02.md", "updated: 2026-09-03", "updated: 2026-10-13")

        def health_log():
            write(self.root, "kit-health/2026-10-14-walk.md", doc_text("Walk", "log", "kit-health", "done", "2026-09-01"))

        def health_stamp():
            plant(self.root, "kit-health/HEALTH-test.md", "updated: 2026-10-09", "updated: 2026-10-15")

        def index_date():
            plant_regex(self.root / "INDEX.md", r"(generated )\d{4}-\d{2}-\d{2}(_)", r"\g<1>2026-10-16\g<2>")

        def session_index_stamp():
            plant_regex(self.root / "SESSION_INDEX.md", r"^(_generated ).+?( · \d+ active)",
                        lambda m: m.group(1) + profile.local_str("2026-10-17T05:00:00Z", ZONE) + m.group(2))

        cases = [(heartbeat, "2026-10-12T08:00:00Z"), (archived_heartbeat, "2026-10-12T08:30:00Z"),
                 (ledger_row, "2026-10-12T09:00:00Z"), (pr_row, "2026-10-12T10:00:00Z"),
                 (sign_job, "2026-10-12T11:00:00Z"), (doc, "2026-10-13T00:00:00Z"),
                 (health_log, "2026-10-14T00:00:00Z"), (health_stamp, "2026-10-15T00:00:00Z"),
                 (index_date, "2026-10-16T00:00:00Z"), (session_index_stamp, "2026-10-17T05:00:00Z")]
        for plant_it, expected in cases:
            with self.subTest(source=plant_it.__name__):
                self.reset()
                self.assertEqual(self.build().returncode, 0)
                self.assertEqual(self.data()["as_of"], AS_OF)
                plant_it()
                self.assertEqual(self.build().returncode, 0)
                self.assertEqual(self.data()["as_of"], expected)

    def test_the_trailing_windows_are_anchored_at_as_of_and_inclusive(self):
        def row(ended):
            return f"| {ended} | `kit-x` | - | 1 | 0.1 | 1k | 1k | 1 | 5 (5+0) | 0 | 0 | 0 | 0 | 0 |\n"
        with open(self.root / "sessions" / "_ledger.md", "a", encoding="utf-8") as f:
            f.write(row("2026-10-03T12:00:00Z"))  # exactly 7 days before as_of: inside
            f.write(row("2026-10-03T11:59:59Z"))  # one second earlier: outside
            f.write(row("2026-09-10T12:00:00Z"))  # exactly 30 days before: inside
            f.write(row("2026-09-10T11:59:59Z"))  # outside
        self.assertEqual(self.build().returncode, 0)
        totals = self.data()["cost"]["totals"]
        self.assertEqual(totals["last_7d"], {"rows": 5, "usd": 127})  # the 4 fixture rows (122) and the edge row
        self.assertEqual(totals["last_30d"], {"rows": 11, "usd": 192})  # 8 fixture rows (177) and the 3 edge rows inside
        self.assertEqual(totals["rows"], 14)


class Deterministic(Store):
    def test_two_builds_of_one_store_differ_by_the_build_stamp_alone(self):
        self.build()
        other = self.tmp / "other"
        self.assertEqual(self.mission("build", "--out", other, "--template", TPL).returncode, 0)
        for name in ("data.json", "last.sha"):
            with self.subTest(name=name):
                self.assertEqual((other / name).read_bytes(), (self.out / name).read_bytes())
        self.assertEqual(unstamped((other / "index.html").read_text(encoding="utf-8")),
                         unstamped(self.text("index.html")))  # the page differs by its build stamp alone
        left, right = self.build_json(), json.loads((other / "build.json").read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in left.items() if k != "generated_at"},
                         {k: v for k, v in right.items() if k != "generated_at"})

    def test_the_base_build_matches_a_fresh_one(self):
        self.build()
        self.assertEqual((self.out / "data.json").read_bytes(), (BUILT / "data.json").read_bytes())

    def test_the_process_time_zone_does_not_matter(self):
        self.build(env=engine_env(self.root, TZ="Etc/GMT+5"))
        self.assertEqual((self.out / "data.json").read_bytes(), (BUILT / "data.json").read_bytes())

    def test_the_display_zone_is_the_configured_one(self):
        self.assertEqual(self.mission("build", "--out", self.out, "--template", TPL,
                                      env=engine_env(self.root, TZ="Etc/GMT-9")).returncode, 0)
        data = self.data()
        self.assertEqual(data["tz"], ZONE_NAME)
        self.assertEqual(data["sessions"]["active"][0]["heartbeat_local"], profile.local_str(AS_OF, ZONE))
        self.assertTrue(data["sessions"]["active"][0]["heartbeat_local"].endswith(" +02"))


class MissingSources(Store):
    def test_absent_sources_are_empty_sections_not_errors(self):
        for rel in ("memory", "reference/priorities.md", "sessions/_ledger.md", "state", "kit-health", "INDEX.md",
                    "SESSION_INDEX.md"):
            path = self.root / rel
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.data()
        self.assertEqual(data["pointers"]["memory"], [])
        self.assertIsNone(data["pointers"]["index"])
        self.assertIsNone(data["pointers"]["session_index"])
        self.assertEqual(data["priorities"], {"items": [], "path": "reference/priorities.md", "present": False})
        self.assertEqual(data["cost"]["ledger"], [])
        self.assertEqual(data["cost"]["totals"], {"by_epic": [], "hours": 0, "last_30d": {"rows": 0, "usd": 0},
                                                  "last_7d": {"rows": 0, "usd": 0}, "rows": 0, "turns": 0, "usd": 0})
        self.assertEqual(data["queues"]["pr_review"], {"by_status": {}, "last": [], "path": "state/pr-review/ledger.jsonl", "rows": 0})
        self.assertEqual(data["queues"]["sign_queue"], {"jobs": [], "path": "state/sign-queue"})
        self.assertEqual(data["queues"]["kit_health"], {"latest": None, "stamp": None})
        present = {s["name"]: s["present"] for s in data["pointers"]["sources"]}
        self.assertEqual({k for k, v in present.items() if not v},
                         {"index", "kit_health", "ledger", "memory", "pr_review", "priorities", "session_index", "sign_queue"})
        self.assertEqual(data["sessions"]["counts"]["active"], 3)  # the rest of the store is read as usual

    def test_a_bare_blank_store_builds_an_empty_page(self):
        bare = self.tmp / "bare" / ".context"
        bare.mkdir(parents=True)
        self.assertEqual(run_tool(BIN / "kb.py", bare, "init", "--blank").returncode, 0)
        r = run_mission(bare, "build", "--out", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.data()
        self.assertEqual(data["sessions"]["counts"], {"active": 0, "archived": 0, "ended": 0, "ended_no_prompt": 0})
        self.assertEqual(data["cost"]["totals"]["rows"], 0)
        self.assertEqual(data["catalog"]["counts"]["docs"], 6)  # the six env docs of a blank store
        self.assertEqual(data["links"]["tracker_kind"], "none")
        self.assertEqual(data["tz"], "UTC")
        self.assertIsNone(data["pointers"]["index"])
        self.assertFalse(data["priorities"]["present"])


def count(text: str):
    """A ledger count cell as the page wants it: `345k` is 345000, `1.2M` is 1200000, `812` is 812."""
    return round(float(text.rstrip("kM")) * {"k": 1000, "M": 1000000}.get(text[-1], 1))


class Sessions(unittest.TestCase):
    def test_the_three_lists_their_order_and_counts(self):
        s = DATA["sessions"]
        self.assertEqual(s["counts"], {"active": 3, "archived": 1, "ended": 2, "ended_no_prompt": 1})
        self.assertEqual([e["name"] for e in s["active"]], ["kit-alpha", "kit-beta", "kit-gamma"])  # newest heartbeat first
        self.assertEqual([e["name"] for e in s["ended"]], ["kit-epsilon", "kit-delta"])
        self.assertEqual([e["name"] for e in s["archived"]], ["kit-zeta"])  # sessions/archive/INDEX.md is not a session
        self.assertEqual(s["problems"], [])
        self.assertTrue(all(e["status"] != "ended" for e in s["active"]))
        self.assertTrue(all(e["status"] == "ended" for e in s["ended"] + s["archived"]))

    def test_fields_and_the_prompt_flag_match_the_registry_parser(self):
        files = sorted((BASE / "sessions").glob("kit-*.md")) + sorted((BASE / "sessions" / "archive").glob("kit-*.md"))
        self.assertEqual(len(files), 6)
        for path in files:
            m = gen_sessions.parse(str(path))
            entry = next(e for key in ("active", "ended", "archived") for e in DATA["sessions"][key]
                         if e["name"] == m["session"])
            with self.subTest(session=m["session"]):
                for key in ("ref", "status", "epic", "working_on", "responsibilities", "heartbeat", "stats"):
                    self.assertEqual(entry[key], m[key], key)
                self.assertEqual(entry["has_prompt"], gen_sessions.has_next_prompt(m))
                self.assertEqual(entry["prompt"], m["_next"])
                self.assertEqual(entry["path"], ".context/" + path.relative_to(BASE).as_posix())
                self.assertEqual(entry["heartbeat_local"], profile.local_str(m["heartbeat"], ZONE))

    def test_the_hand_off_prompts(self):
        by_name = {e["name"]: e for key in ("active", "ended", "archived") for e in DATA["sessions"][key]}
        self.assertTrue(by_name["kit-delta"]["has_prompt"])
        self.assertEqual(by_name["kit-delta"]["prompt"],
                         NEXT_PROMPT)
        self.assertFalse(by_name["kit-epsilon"]["has_prompt"])
        self.assertEqual(by_name["kit-epsilon"]["prompt"], "")

    def test_the_cost_of_each_session_is_its_total(self):
        self.assertEqual([e["cost_usd"] for e in DATA["sessions"]["active"]], [44, 3.5, None])
        self.assertEqual(DATA["sessions"]["active"][0]["stats"], "100 turns · 2.5h · ctx peak 345k · ~$44 (30+14)")

    def test_working_on_is_the_plain_text_and_the_zone_the_configured_one(self):
        by_name = {e["name"]: e for e in DATA["sessions"]["active"]}
        self.assertEqual(by_name["kit-beta"]["working_on"], "wire the export: step 2")  # its quotes are not part of it
        self.assertEqual(by_name["kit-alpha"]["heartbeat_local"], "2026-10-10 02:00 PM +02")
        self.assertEqual(by_name["kit-alpha"]["repos"], ["acme/widgets", "acme/gadgets"])

    def test_open_prs_and_crons(self):
        by_name = {e["name"]: e for key in ("active", "ended", "archived") for e in DATA["sessions"][key]}
        self.assertEqual(by_name["kit-beta"]["open_prs"], [
            {"number": 7, "repo": "acme/widgets", "text": "acme/widgets#7 — draft, waiting on CI",
             "url": "https://github.com/acme/widgets/pull/7"},
            {"number": 8, "repo": "acme/widgets", "text": "widgets#8 follow-up", "url": "https://github.com/acme/widgets/pull/8"},
            {"number": 9, "repo": "acme/widgets", "text": "#9", "url": "https://github.com/acme/widgets/pull/9"}])
        self.assertEqual(by_name["kit-beta"]["crons"], ["- `*/30 * * * *` poll the review queue", "- `0 9 * * 1-5` standup reminder"])
        for name, entry in by_name.items():
            if name != "kit-beta":
                self.assertEqual((entry["open_prs"], entry["crons"]), ([], []), name)

    def test_stale_is_decided_against_as_of_by_the_registry_rule(self):
        as_of = mission.parse_utc(DATA["as_of"])
        stale = {e["name"]: mission.is_stale(e["heartbeat"], as_of) for e in DATA["sessions"]["active"]}
        self.assertEqual(stale, {"kit-alpha": False, "kit-beta": False, "kit-gamma": True})


class SessionFiles(Store):
    def names(self, key: str) -> list:
        return [e["name"] for e in self.data()["sessions"][key]]

    def test_files_that_are_not_sessions_are_skipped(self):
        write(self.root, "sessions/.hidden.md", session_doc("hidden", "active", AS_OF))
        write(self.root, "sessions/_notes.md", session_doc("notes", "active", AS_OF))
        write(self.root, "sessions/readme.txt", "not a session\n")
        (self.root / "sessions" / "folder.md").mkdir()
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.data()["sessions"]["counts"], {"active": 3, "archived": 1, "ended": 2, "ended_no_prompt": 1})

    def test_a_file_that_is_not_utf8_is_reported_not_fatal(self):
        (self.root / "sessions" / "kit-bad.md").write_bytes(b"---\nsession: kit-bad\nstatus: active\n---\n\xff\xfe\n")
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.data()["sessions"]["problems"], [".context/sessions/kit-bad.md: cannot read — not valid UTF-8"])
        self.assertEqual(self.names("active"), ["kit-alpha", "kit-beta", "kit-gamma"])

    def test_equal_heartbeats_are_ordered_by_name_and_a_nameless_file_takes_its_stem(self):
        write(self.root, "sessions/kit-b2.md", session_doc("kit-b2", "active", "2026-10-09T07:00:00Z"))
        write(self.root, "sessions/kit-a2.md", session_doc("kit-a2", "active", "2026-10-09T07:00:00Z"))
        write(self.root, "sessions/kit-stem.md", "---\nstatus: active\nheartbeat: 2026-10-09T07:00:00Z\n---\n\n# no name\n")
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.names("active"), ["kit-alpha", "kit-beta", "kit-a2", "kit-b2", "kit-stem", "kit-gamma"])

    def test_a_no_successor_note_is_no_prompt_but_stays_verbatim(self):
        note = "None - the work is finished."
        write(self.root, "sessions/kit-done.md", session_doc("kit-done", "ended", "2026-10-09T21:00:00Z", prompt=note))
        self.assertEqual(self.build().returncode, 0)
        entry = next(e for e in self.data()["sessions"]["ended"] if e["name"] == "kit-done")
        m = gen_sessions.parse(str(self.root / "sessions" / "kit-done.md"))
        self.assertEqual(entry["has_prompt"], gen_sessions.has_next_prompt(m))
        self.assertEqual(entry["prompt"], m["_next"])


class Cost(unittest.TestCase):
    def test_every_ledger_row_is_read_in_file_order(self):
        rows = DATA["cost"]["ledger"]
        self.assertEqual(len(rows), len(LEDGER_ROWS))
        for i, (row, planted) in enumerate(zip(rows, LEDGER_ROWS)):
            ended, name, epic, turns, hours, ctx, cache, out, (total, main, sub), comp, prs, tickets, sign, drafts = planted
            with self.subTest(row=i, session=name):
                self.assertEqual(row["session"], name)
                self.assertEqual(row["epic"], "" if epic == "-" else epic)
                self.assertEqual((row["turns"], row["hours"]), (turns, hours))
                self.assertEqual((row["ctx_peak"], row["cache_read"], row["out"]), (count(ctx), count(cache), count(out)))
                self.assertEqual((row["usd"], row["usd_split"]), (total, f"{main}+{sub}"))
                self.assertEqual((row["compactions"], row["prs"], row["tickets"], row["sign_jobs"], row["drafts"]),
                                 (comp, prs, tickets, sign, drafts))

    def test_the_end_time_is_read_back_to_utc(self):
        rows = DATA["cost"]["ledger"]
        for i in range(LOCALIZED_ROWS):
            with self.subTest(row=i):
                self.assertEqual(rows[i]["ended"], profile.local_str(LEDGER_ROWS[i][0], ZONE))  # the cell as written
                self.assertEqual(rows[i]["ended_utc"], LEDGER_ROWS[i][0])
        self.assertEqual((rows[8]["ended"], rows[8]["ended_utc"]), ("2026-09-05T10:00:00Z", "2026-09-05T10:00:00Z"))
        self.assertEqual((rows[9]["ended"], rows[9]["ended_utc"]), ("long ago", None))

    def test_totals_are_the_sum_of_the_planted_cells(self):
        t = DATA["cost"]["totals"]
        self.assertEqual(sum(r[8][0] for r in LEDGER_ROWS), LEDGER_USD)
        self.assertEqual(t["usd"], LEDGER_USD)
        self.assertEqual(t["rows"], len(LEDGER_ROWS))
        self.assertEqual(t["turns"], sum(r[3] for r in LEDGER_ROWS))
        self.assertEqual(t["hours"], sum(r[4] for r in LEDGER_ROWS))
        self.assertEqual(t["usd"], sum(r["usd"] for r in DATA["cost"]["ledger"]))

    def test_by_epic_groups_the_rows_biggest_first(self):
        groups = {}
        for r in LEDGER_ROWS:
            groups.setdefault("" if r[2] == "-" else r[2], []).append(r[8][0])
        expected = sorted(({"epic": e, "rows": len(v), "usd": sum(v)} for e, v in groups.items()),
                          key=lambda g: (-g["usd"], g["epic"]))
        by_epic = DATA["cost"]["totals"]["by_epic"]
        self.assertEqual([{k: g[k] for k in ("epic", "rows", "usd")} for g in by_epic], expected)
        self.assertEqual([g["epic"] for g in by_epic], ["KEY-123", "", "acme/widgets#42", "KEY-456"])
        self.assertEqual(sum(g["usd"] for g in by_epic), DATA["cost"]["totals"]["usd"])
        self.assertEqual([g["epic_url"] for g in by_epic], [
            "https://tracker.example/browse/KEY-123", None, "https://github.com/acme/widgets/issues/42",
            "https://tracker.example/browse/KEY-456"])

    def test_the_windows_end_at_as_of_not_at_the_clock(self):
        t = DATA["cost"]["totals"]
        # as_of is 2026-10-10 12:00: the 7-day window starts 10-03 12:00 (rows ended 10-09, 10-08, 10-06, 10-04:
        # 40+32+30+20), the 30-day one 09-10 12:00 (those four and 10-02, 09-28, 09-20, 09-12: 15+14+13+13)
        self.assertEqual(t["last_7d"], {"rows": 4, "usd": 122})
        self.assertEqual(t["last_30d"], {"rows": 8, "usd": 177})

    def test_the_basis_and_the_live_rows(self):
        cost = DATA["cost"]
        self.assertEqual(cost["basis"], session_stats.SPEND_BASIS)
        self.assertEqual(cost["live"], [{"cost_usd": e["cost_usd"], "name": e["name"], "stats": e["stats"]}
                                        for e in DATA["sessions"]["active"]])


class CostPlanted(Store):
    def test_a_sum_never_prints_float_noise(self):
        with open(self.root / "sessions" / "_ledger.md", "a", encoding="utf-8") as f:
            for usd in ("0.1", "0.2"):
                f.write(f"| 2026-10-09T10:00:00Z | `kit-x` | - | 1 | 0.1 | 1k | 1k | 1 | {usd} ({usd}+0) | 0 | 0 | 0 | 0 | 0 |\n")
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.data()["cost"]["totals"]["usd"], 232.3)
        self.assertRegex(self.text("data.json"), r'"usd": 232\.3\n')  # `usd` is the last key of the totals

    def test_a_row_with_a_dash_or_text_in_a_number_cell_has_a_null_there(self):
        with open(self.root / "sessions" / "_ledger.md", "a", encoding="utf-8") as f:
            f.write("| 2026-10-09T10:00:00Z | `kit-x` | - | - | n/a | - | - | - | - | - | - | - | - | - |\n")
        self.assertEqual(self.build().returncode, 0)
        row = self.data()["cost"]["ledger"][-1]
        self.assertEqual(row["session"], "kit-x")
        for key in ("turns", "hours", "ctx_peak", "cache_read", "out", "usd", "compactions", "prs", "tickets", "sign_jobs", "drafts"):
            self.assertIsNone(row[key], key)
        self.assertEqual(row["usd_split"], "")
        self.assertEqual(self.data()["cost"]["totals"]["usd"], LEDGER_USD)


class Links(unittest.TestCase):
    def test_the_links_block_is_the_tracker_config(self):
        self.assertEqual(DATA["links"], {"github_org": "acme", "repos": ["acme/widgets"], "tracker_kind": "jira",
                                         "tracker_url_template": TRACKER_TEMPLATE})

    def test_epics_resolve_to_urls(self):
        by_name = {e["name"]: e for key in ("active", "ended") for e in DATA["sessions"][key]}
        self.assertEqual(by_name["kit-alpha"]["epic_url"], "https://tracker.example/browse/KEY-123")
        self.assertEqual(by_name["kit-beta"]["epic_url"], "https://github.com/acme/widgets/issues/42")
        self.assertEqual(by_name["kit-delta"]["epic_url"], "https://tracker.example/browse/KEY-456")
        self.assertIsNone(by_name["kit-gamma"]["epic_url"])  # no epic

    def test_a_pr_reference_resolves_to_its_pull_request(self):
        beta = next(e for e in DATA["sessions"]["active"] if e["name"] == "kit-beta")
        self.assertEqual(beta["open_prs"][0]["url"], "https://github.com/acme/widgets/pull/7")
        last = DATA["queues"]["pr_review"]["last"]
        self.assertEqual([r["url"] for r in last], ["https://github.com/acme/widgets/pull/7",
                                                    "https://github.com/acme/widgets/pull/8",
                                                    "https://github.com/acme/widgets/pull/7"])  # `widgets` sits under github.org

    def test_no_url_holds_a_workspace_path(self):
        text = (BUILT / "data.json").read_text(encoding="utf-8")
        for m in re.finditer(r'"[a-z_]*url": "([^"]*)"', text):
            self.assertRegex(m.group(1), r"^https://", m.group(0))
        self.assertNotIn("/home/", text)


class LinkConfigs(Store):
    def configure(self, **values) -> None:
        for key, value in values.items():
            r = run_tool(BIN / "kb.py", self.root, "config-set", key.replace("__", "."), value)
            self.assertEqual(r.returncode, 0, r.stderr)

    def epic_urls(self) -> dict:
        self.assertEqual(self.build().returncode, 0)
        return {e["name"]: e["epic_url"] for key in ("active", "ended") for e in self.data()["sessions"][key]}

    def test_a_github_tracker_resolves_issue_refs_and_leaves_ticket_keys_unresolved(self):
        self.configure(tracker__kind="github", tracker__url_template="https://tracker.example/{repo}/issues/{key}")
        urls = self.epic_urls()
        self.assertEqual(urls["kit-beta"], "https://tracker.example/acme/widgets/issues/42")
        self.assertIsNone(urls["kit-alpha"])  # KEY-123 needs a template with no {repo} to fill
        self.assertIsNone(urls["kit-delta"])

    def test_no_template_still_resolves_an_issue_ref_to_github(self):
        self.configure(tracker__url_template="")
        urls = self.epic_urls()
        self.assertEqual(urls["kit-beta"], "https://github.com/acme/widgets/issues/42")
        self.assertIsNone(urls["kit-alpha"])

    def test_the_links_block_follows_the_config(self):
        self.configure(github__org="", tracker__repos="[]")
        self.assertEqual(self.build().returncode, 0)
        links = self.data()["links"]
        self.assertEqual((links["github_org"], links["repos"]), ("", []))
        pr = next(r for r in self.data()["queues"]["pr_review"]["last"] if r["repo"] == "widgets")
        self.assertIsNone(pr["url"])  # a bare repo name cannot be placed without an org


class Catalog(unittest.TestCase):
    def test_docs_are_sorted_by_path_and_carry_their_frontmatter(self):
        docs = DATA["catalog"]["docs"]
        self.assertEqual(len(docs), 50)
        self.assertEqual([d["path"] for d in docs], sorted(d["path"] for d in docs))
        by_path = {d["path"]: d for d in docs}
        one = by_path["repos/repos-01.md"]
        self.assertEqual({k: one[k] for k in ("title", "type", "domain", "status", "tags", "updated")},
                         {"title": "Repos note 01", "type": "log", "domain": "repos", "status": "reference",
                          "tags": ["fixture", "repos"], "updated": "2026-09-02"})
        self.assertEqual(one["bytes"], (BASE / "repos" / "repos-01.md").stat().st_size)
        self.assertEqual(by_path["repos/repos-00.md"]["title"], SSH_TITLE)

    def test_large_means_over_thirty_kib(self):
        by_path = {d["path"]: d for d in DATA["catalog"]["docs"]}
        self.assertEqual((by_path["notes/notes-00.md"]["bytes"], by_path["notes/notes-00.md"]["large"]), (31 * 1024, True))
        self.assertEqual((by_path["notes/notes-01.md"]["bytes"], by_path["notes/notes-01.md"]["large"]), (30 * 1024, False))
        self.assertEqual([d["path"] for d in DATA["catalog"]["docs"] if d["large"]], ["notes/notes-00.md"])

    def test_domains_and_counts_add_up(self):
        catalog = DATA["catalog"]
        self.assertEqual([(d["domain"], d["docs"]) for d in catalog["domains"]],
                         [("kit-health", 3), ("meetings", 13), ("notes", 13), ("reference", 7), ("repos", 14)])
        for d in catalog["domains"]:
            with self.subTest(domain=d["domain"]):
                self.assertEqual(d["bytes"], sum(x["bytes"] for x in catalog["docs"] if x["domain"] == d["domain"]))
        counts = catalog["counts"]
        self.assertEqual(counts["docs"], 50)
        self.assertEqual(sum(d["docs"] for d in catalog["domains"]), 50)
        self.assertEqual(sum(counts["by_status"].values()), 50)
        self.assertEqual(sum(counts["by_type"].values()), 50)
        self.assertEqual(sorted(counts["by_status"]), ["active", "done", "draft", "reference"])
        self.assertEqual(sorted(counts["by_type"]), ["log", "note", "reference"])
        self.assertEqual(catalog["problems"], [])

    def test_the_catalog_skips_what_the_index_skips(self):
        paths = {d["path"] for d in DATA["catalog"]["docs"]}
        self.assertFalse({p for p in paths if p.startswith(("sessions/", "memory/", "state/", "handoff/"))}, paths)
        self.assertNotIn("INDEX.md", paths)
        self.assertNotIn("SESSION_INDEX.md", paths)

    def test_the_index_pointer_agrees_with_the_catalog(self):
        self.assertEqual(DATA["pointers"]["index"], {"docs": DATA["catalog"]["counts"]["docs"], "generated": INDEX_DATE})


class CatalogProblems(Store):
    def test_a_broken_doc_is_listed_and_the_rest_is_read(self):
        write(self.root, "repos/broken.md", "# no frontmatter at all\n")
        write(self.root, "repos/partial.md", "---\ntitle: Partial\ntype: note\ndomain: repos\n---\n\nbody\n")
        (self.root / "repos" / "bad.md").write_bytes(b"---\ntitle: Bad\n---\n\xff\n")
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        catalog = self.data()["catalog"]
        self.assertEqual(len(catalog["problems"]), 3)
        self.assertEqual(catalog["problems"][0], "repos/bad.md: not valid UTF-8 (byte b'\\xff' at offset 19) — re-save the file as UTF-8")
        self.assertEqual(catalog["problems"][1], "repos/broken.md: no frontmatter block")
        self.assertEqual(catalog["problems"][2], "repos/partial.md: missing status, updated")
        self.assertEqual(catalog["problems"], sorted(catalog["problems"]))
        paths = {d["path"] for d in catalog["docs"]}
        self.assertIn("repos/partial.md", paths)  # a doc with a gap is still a row
        self.assertNotIn("repos/broken.md", paths)
        self.assertEqual(catalog["counts"]["docs"], 51)
        self.assertEqual(catalog["counts"]["by_status"]["?"], 1)


class Priorities(unittest.TestCase):
    def test_checkbox_items_with_their_section(self):
        p = DATA["priorities"]
        self.assertEqual((p["present"], p["path"]), (True, "reference/priorities.md"))
        self.assertEqual([(i["section"], i["done"], i["text"]) for i in p["items"]], [
            ("", False, "Orphan item before any heading"),
            ("Now", False, "Ship the widget exporter"),
            ("Now", True, "Close the audit ticket"),
            ("Later", False, "Tidy the backlog"),
            ("Later", True, "Archive the old notes")])  # `* [X]` counts; the item inside the code fence does not
        self.assertEqual(sorted(p["items"][0]), ["done", "section", "text"])


class Queues(unittest.TestCase):
    def test_pr_review_rows_and_counts(self):
        q = DATA["queues"]["pr_review"]
        self.assertEqual((q["path"], q["rows"], q["by_status"]), ("state/pr-review/ledger.jsonl", 3, {"posted": 2, "skipped": 1}))
        self.assertEqual([(r["repo"], r["pr"], r["head"], r["status"], r["ts"]) for r in q["last"]], [
            ("acme/widgets", 7, "abc1234", "posted", "2026-10-07T09:00:00Z"),
            ("acme/widgets", 8, "def5678", "skipped", "2026-10-08T09:00:00Z"),  # its "8" is a string in the ledger
            ("widgets", 7, "0123abc", "posted", "2026-10-09T09:00:00Z")])  # the "not json" line is skipped

    def test_kit_health_latest_walk_and_stamp(self):
        q = DATA["queues"]["kit_health"]
        self.assertEqual(q["latest"], {
            "date": "2026-10-09", "path": "kit-health/2026-10-09-walk.md",
            "outcome": "## Outcome of the walk — 2 findings, 0 blockers\nFiled two follow-ups; nothing blocks the release."})
        self.assertEqual(q["stamp"], {"path": "kit-health/HEALTH-test.md", "updated": "2026-10-09"})

    def test_sign_queue_is_empty_in_the_fixture(self):
        self.assertEqual(DATA["queues"]["sign_queue"], {"jobs": [], "path": "state/sign-queue"})


class QueuesPlanted(Store):
    def test_only_the_last_ten_review_rows_are_listed(self):
        rows = [{"repo": "acme/widgets", "pr": n, "head": f"h{n}", "status": "posted", "ts": f"2026-10-0{1 + n % 9}T08:00:00Z"}
                for n in range(100, 112)]
        write(self.root, "state/pr-review/ledger.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
        self.assertEqual(self.build().returncode, 0)
        q = self.data()["queues"]["pr_review"]
        self.assertEqual((q["rows"], q["by_status"]), (12, {"posted": 12}))
        self.assertEqual([r["pr"] for r in q["last"]], list(range(102, 112)))

    def test_sign_queue_lists_the_scripts_only_and_sets_as_of(self):
        queue = self.root / "state" / "sign-queue"
        newer = datetime(2026, 10, 12, 11, 0, 0, tzinfo=timezone.utc).timestamp()
        for name in ("b-second.sh", "a-first.sh"):
            os.utime(write(queue, name, "echo placeholder\n"), (newer, newer))
        write(queue, "notes.txt", "not a job\n")
        (queue / "folder.sh").mkdir()
        self.assertEqual(self.build().returncode, 0)
        data = self.data()
        self.assertEqual(data["queues"]["sign_queue"]["jobs"], [
            {"mtime_utc": "2026-10-12T11:00:00Z", "name": "a-first.sh"}, {"mtime_utc": "2026-10-12T11:00:00Z", "name": "b-second.sh"}])
        self.assertEqual(data["as_of"], "2026-10-12T11:00:00Z")

    def test_a_walk_log_without_an_outcome_has_an_empty_one(self):
        write(self.root, "kit-health/2026-10-11-walk.md", doc_text("Walk", "log", "kit-health", "done", "2026-10-11", "Nothing to report."))
        self.assertEqual(self.build().returncode, 0)
        latest = self.data()["queues"]["kit_health"]["latest"]
        self.assertEqual((latest["date"], latest["outcome"]), ("2026-10-11", ""))

    def test_an_outcome_heading_alone_is_kept(self):
        write(self.root, "kit-health/2026-10-11-walk.md", doc_text(
            "Walk", "log", "kit-health", "done", "2026-10-11", "## Outcome of the walk — clean\n\n## Next steps\n\nNone."))
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.data()["queues"]["kit_health"]["latest"]["outcome"], "## Outcome of the walk — clean")


class Pointers(unittest.TestCase):
    def test_memory_pointers(self):
        self.assertEqual(DATA["pointers"]["memory"], [
            {"file": "user-identity.md", "hook": "a placeholder person with a placeholder role", "title": "User identity"},
            {"file": "build-habits.md", "hook": "run the full CI target before pushing", "title": "Build habits"},
            {"file": "plain.md", "hook": "", "title": "Plain pointer"}])

    def test_the_session_index_line(self):
        self.assertEqual(DATA["pointers"]["session_index"], {
            "active": 3, "ended_listed": 1, "ended_no_prompt": 1, "generated": profile.local_str(SESSION_INDEX_STAMP, ZONE)})
        counts = DATA["sessions"]["counts"]
        self.assertEqual(DATA["pointers"]["session_index"]["active"], counts["active"])
        self.assertEqual(DATA["pointers"]["session_index"]["ended_no_prompt"], counts["ended_no_prompt"])

    def test_every_source_is_listed_by_name_with_its_path(self):
        sources = DATA["pointers"]["sources"]
        self.assertEqual([s["name"] for s in sources], sorted(s["name"] for s in sources))
        self.assertEqual({s["name"]: s["path"] for s in sources}, {
            "catalog": ".", "index": "INDEX.md", "kit_health": "kit-health", "ledger": "sessions/_ledger.md",
            "memory": "memory/MEMORY.md", "pr_review": "state/pr-review/ledger.jsonl",
            "priorities": "reference/priorities.md", "session_index": "SESSION_INDEX.md", "sessions": "sessions",
            "sign_queue": "state/sign-queue"})
        self.assertTrue(all(s["present"] for s in sources))


# ── pure helpers, called in process ───────────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def link_config(template: str = "", org: str = "", repos=()):
    """The link rules read tracker.url_template, github.org and tracker.repos through two helpers: patch them, so a
    case needs no store (this suite's own import is pinned to another one)."""
    values = {"tracker.url_template": template, "github.org": org}
    with mock.patch.object(mission, "conf", lambda key: values.get(key, "")):
        with mock.patch.object(mission, "config_repos", lambda: list(repos)):
            yield


class FrozenClock:
    """Stands in for gen_sessions.datetime: now() is fixed, strptime is the real one."""

    def __init__(self, now: datetime):
        self._now = now

    def now(self, tz=None):
        return self._now.astimezone(tz) if tz else self._now.replace(tzinfo=None)

    strptime = staticmethod(datetime.strptime)


class IssueUrl(unittest.TestCase):
    KEYED = TRACKER_TEMPLATE
    GITHUB = "https://github.com/{repo}/issues/{key}"
    OTHER_HOST = "https://git.example/{repo}/tickets/{key}"

    def test_a_ticket_key_fills_a_key_template(self):
        with link_config(self.KEYED):
            self.assertEqual(mission.issue_url("KEY-123"), "https://tracker.example/browse/KEY-123")
            for ref in ("KEY-123, KEY-456", "KEY-123; KEY-456", "KEY-123 and more words"):
                self.assertEqual(mission.issue_url(ref), "https://tracker.example/browse/KEY-123", ref)  # the first token

    def test_a_key_needs_a_template_with_no_repo_to_fill(self):
        for template in ("", self.GITHUB, self.OTHER_HOST):
            with link_config(template, org="acme", repos=["acme/widgets"]):
                self.assertIsNone(mission.issue_url("KEY-123"), template)

    def test_text_that_is_not_a_key_or_ref_resolves_to_nothing(self):
        with link_config(self.KEYED, org="acme", repos=["acme/widgets"]):
            for ref in ("", "  ", "-", None, "key-123", "KEY123", "KEY-", "KEY-123.", "plan the export", "#", "#x", "42"):
                self.assertIsNone(mission.issue_url(ref), repr(ref))

    def test_an_issue_ref_is_a_github_issue_whatever_the_template_is(self):
        for template in ("", self.KEYED):
            with link_config(template, org="acme", repos=["acme/widgets"]):
                for ref in ("acme/widgets#42", "widgets#42", "#42"):
                    self.assertEqual(mission.issue_url(ref), "https://github.com/acme/widgets/issues/42", (template, ref))

    def test_a_repo_template_is_filled_for_an_issue_ref(self):
        with link_config(self.OTHER_HOST, org="acme", repos=["acme/widgets"]):
            for ref in ("acme/widgets#42", "widgets#42", "#42"):
                self.assertEqual(mission.issue_url(ref), "https://git.example/acme/widgets/tickets/42", ref)
        with link_config(self.GITHUB, org="acme", repos=["acme/widgets"]):
            self.assertEqual(mission.issue_url("acme/gadgets#7"), "https://github.com/acme/gadgets/issues/7")

    def test_a_ref_that_names_no_owner_needs_the_config(self):
        with link_config(self.KEYED):  # no org, no repos
            self.assertEqual(mission.issue_url("acme/widgets#42"), "https://github.com/acme/widgets/issues/42")
            self.assertIsNone(mission.issue_url("widgets#42"))
            self.assertIsNone(mission.issue_url("#42"))
        with link_config(self.KEYED, org="acme"):  # an org places a bare repo name, not a bare number
            self.assertEqual(mission.issue_url("widgets#42"), "https://github.com/acme/widgets/issues/42")
            self.assertIsNone(mission.issue_url("#42"))
        with link_config(self.KEYED, repos=["widgets"]):  # a repo entry with no owner cannot be placed either
            self.assertIsNone(mission.issue_url("#42"))

    def test_the_first_tracker_repo_places_a_bare_number(self):
        with link_config(self.KEYED, org="acme", repos=["acme/gadgets", "acme/widgets"]):
            self.assertEqual(mission.issue_url("#42"), "https://github.com/acme/gadgets/issues/42")

    def test_a_web_url_passes_through_and_nothing_else_does(self):
        with link_config(self.KEYED):
            for url in ("https://tracker.example/browse/KEY-9", "http://tracker.example/x?a=1&b=2#frag"):
                self.assertEqual(mission.issue_url(url), url)
            self.assertEqual(mission.issue_url("https://tracker.example/x and more"), "https://tracker.example/x")
            for ref in ("javascript:alert(1)", "ftp://tracker.example/x", "file:///etc/hosts", "data:text/html,x",
                        "//tracker.example/x", "https://tracker.example/<b>", 'https://tracker.example/"x',
                        "https://tracker.example/'x", "https://"):
                self.assertIsNone(mission.issue_url(ref), ref)


class OpenPrs(unittest.TestCase):
    def test_each_listed_pr_is_one_entry_with_its_marker_stripped(self):
        with link_config(org="acme", repos=["acme/widgets"]):
            got = mission.open_prs("- acme/widgets#7 — draft, waiting on CI\n* widgets#8 follow-up\n1. #9\n+ #10\n2) #11")
        self.assertEqual([(p["repo"], p["number"], p["url"]) for p in got], [
            ("acme/widgets", n, f"https://github.com/acme/widgets/pull/{n}") for n in (7, 8, 9, 10, 11)])
        self.assertEqual([p["text"] for p in got], [
            "acme/widgets#7 — draft, waiting on CI", "widgets#8 follow-up", "#9", "#10", "#11"])

    def test_a_pull_request_url_is_read_as_it_is(self):
        got = mission.open_prs("- https://github.com/acme/gadgets/pull/12 (ready for review)")
        self.assertEqual(got, [{"repo": "acme/gadgets", "number": 12, "url": "https://github.com/acme/gadgets/pull/12",
                                "text": "https://github.com/acme/gadgets/pull/12 (ready for review)"}])

    def test_nothing_listed_gives_nothing(self):
        with link_config(org="acme", repos=["acme/widgets"]):
            for section in ("none", "None.", "- none", "", "\n\n", "  ", "- waiting for the review bot\n- nothing else",
                            "- https://github.com/acme/widgets/issues/12 is an issue, not a PR"):
                self.assertEqual(mission.open_prs(section), [], repr(section))

    def test_without_the_config_only_a_full_ref_has_a_url(self):
        with link_config():
            got = mission.open_prs("- #9\n- widgets#8\n- acme/widgets#7")
        self.assertEqual([(p["repo"], p["number"], p["url"]) for p in got], [
            ("", 9, None), ("widgets", 8, None), ("acme/widgets", 7, "https://github.com/acme/widgets/pull/7")])

    def test_a_ref_inside_a_longer_path_or_word_is_not_a_pr(self):
        with link_config(org="acme", repos=["acme/widgets"]):
            for line in ("- see docs/notes/plan#4 for the plan", "- http://tracker.example/x#4", "- build#4x"):
                self.assertEqual(mission.open_prs(line), [], line)


class Numbers(unittest.TestCase):
    def test_a_cell_that_is_a_number(self):
        for cell, want in (("12", 12), ("0.5", 0.5), (".5", 0.5), ("345k", 345000), ("2K", 2000), ("1.2M", 1200000),
                           ("3m", 3000000), ("~$44", 44), ("$1,234.5", 1234.5), ("  7 ", 7), ("0", 0)):
            self.assertEqual(mission.number(cell), want, cell)

    def test_a_cell_that_is_not_a_number(self):
        for cell in ("-", "", None, "n/a", "abc", "48 (23+25)", "~$44 (30+14)", "12 turns", "1.2.3", "--5"):
            self.assertIsNone(mission.number(cell), repr(cell))

    def test_the_leading_number_when_the_cell_may_carry_more(self):
        self.assertEqual(mission.number("48 (23+25)", whole=False), 48)
        self.assertEqual(mission.number("~$44 (30+14)", whole=False), 44)
        self.assertEqual(mission.number("12 turns", whole=False), 12)
        self.assertIsNone(mission.number("abc", whole=False))
        self.assertIsNone(mission.number("", whole=False))

    def test_a_whole_number_is_an_int_and_a_sum_has_no_float_noise(self):
        self.assertEqual(mission.tidy(0.1 + 0.2), 0.3)
        self.assertEqual(mission.tidy(232.29999999999998), 232.3)
        self.assertEqual(mission.tidy(3.0), 3)
        self.assertIsInstance(mission.tidy(3.0), int)
        self.assertIsInstance(mission.number("12"), int)
        self.assertIsInstance(mission.number("0.5"), float)


class ParseLocal(unittest.TestCase):
    def test_a_rendered_time_reads_back_to_its_utc_stamp(self):
        with mock.patch.object(mission, "ZONE", ZONE):
            for utc in ("2026-10-10T12:00:00Z", "2026-01-02T00:00:00Z", "2026-12-31T23:59:00Z", "2026-03-08T07:05:00Z"):
                shown = profile.local_str(utc, ZONE)
                self.assertNotEqual(shown, utc)
                self.assertEqual(mission.parse_local(shown), utc, shown)

    def test_what_is_not_a_rendered_time_gives_none(self):
        with mock.patch.object(mission, "ZONE", ZONE):
            for text in (None, "", "  ", "long ago", "2026-10-10", "2026-10-10T12:00:00Z", "2026-10-10 02:00 PM",
                         "2026-13-40 02:00 PM +02", "10/10/2026 02:00 PM +02", "2026-10-10 14:00 +02"):
                self.assertIsNone(mission.parse_local(text), repr(text))

    def test_another_zone_name_is_read_in_the_display_zone(self):
        with mock.patch.object(mission, "ZONE", ZONE):  # written while another zone was configured
            self.assertEqual(mission.parse_local("2026-10-10 02:00 PM XYZ"), "2026-10-10T12:00:00Z")

    def test_the_zone_name_picks_the_repeated_hour_of_a_clock_change(self):
        try:
            troll = ZoneInfo("Antarctica/Troll")  # +02 in summer, +00 in winter: 01:30 happens twice on 2026-10-25
        except ZoneInfoNotFoundError:
            self.skipTest("this host's tz database has no such zone")
        first, second = "2026-10-24T23:30:00Z", "2026-10-25T01:30:00Z"
        self.assertEqual((profile.local_str(first, troll), profile.local_str(second, troll)),
                         ("2026-10-25 01:30 AM +02", "2026-10-25 01:30 AM +00"))
        with mock.patch.object(mission, "ZONE", troll):
            self.assertEqual(mission.parse_local("2026-10-25 01:30 AM +02"), first)
            self.assertEqual(mission.parse_local("2026-10-25 01:30 AM +00"), second)


class IsStale(unittest.TestCase):
    BEAT = "2026-10-10T00:00:00Z"
    OFFSETS = (timedelta(hours=-1), timedelta(0), timedelta(hours=1), timedelta(hours=11), timedelta(hours=12, seconds=-1),
               timedelta(hours=12), timedelta(hours=12, seconds=1), timedelta(hours=13), timedelta(hours=40))

    def test_it_applies_the_rule_the_session_index_applies(self):
        beat = mission.parse_utc(self.BEAT)
        for offset in self.OFFSETS:
            now = beat + offset
            with mock.patch.object(gen_sessions, "datetime", FrozenClock(now)):
                age = gen_sessions.age_hours(self.BEAT)
            self.assertEqual(mission.is_stale(self.BEAT, now), age > gen_sessions.STALE_HOURS, offset)

    def test_more_than_the_stale_hours_old_is_stale_exactly_that_old_is_not(self):
        beat = mission.parse_utc(self.BEAT)
        self.assertEqual(gen_sessions.STALE_HOURS, 12)
        self.assertFalse(mission.is_stale(self.BEAT, beat + timedelta(hours=12)))
        self.assertTrue(mission.is_stale(self.BEAT, beat + timedelta(hours=12, seconds=1)))
        self.assertFalse(mission.is_stale(self.BEAT, beat - timedelta(hours=3)))  # a heartbeat from the future

    def test_a_heartbeat_that_is_not_a_utc_stamp_is_not_stale(self):
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        for beat in ("", None, "yesterday", "2026-10-10", "2026-10-10 00:00 AM +02"):
            self.assertFalse(mission.is_stale(beat, now), repr(beat))

    def test_a_naive_now_is_utc_and_an_aware_one_is_compared_as_the_instant_it_is(self):
        self.assertTrue(mission.is_stale(self.BEAT, datetime(2026, 10, 10, 13, 0, 0)))
        self.assertFalse(mission.is_stale(self.BEAT, datetime(2026, 10, 10, 11, 0, 0)))
        self.assertTrue(mission.is_stale(self.BEAT, datetime(2026, 10, 10, 15, 0, 0, tzinfo=ZONE)))  # 13:00 UTC
        self.assertFalse(mission.is_stale(self.BEAT, datetime(2026, 10, 10, 13, 0, 0, tzinfo=ZONE)))  # 11:00 UTC


class PageText(unittest.TestCase):
    def test_script_safe_escapes_only_what_could_end_the_block(self):
        text = mission.dumps({"a": "</script><b>", "b": "line sep end", "c": "plain é text"})
        safe = mission.script_safe(text)
        for raw in ("<", " ", " "):
            self.assertNotIn(raw, safe)
        self.assertIn("é", safe)  # other characters stay as they are
        self.assertEqual(json.loads(safe), json.loads(text))

    def test_render_fills_every_token_in_a_single_pass(self):
        data_text = mission.dumps({"note": "{{BUILD_JSON}} {{AS_OF}} <b>"})  # store text that looks like a token
        build_text = mission.dumps({"k": "{{DATA_JSON}}"})
        page = mission.render("A {{AS_OF}} | {{DATA_JSON}} | {{BUILD_JSON}} | {{DATA_PRETTY}} | {{OTHER}}",
                              data_text, build_text, AS_OF)
        self.assertEqual(page, f"A {AS_OF} | {mission.script_safe(data_text)} | {mission.script_safe(build_text)} | "
                               f"{html.escape(data_text)} | {{{{OTHER}}}}")
        self.assertIn("{{BUILD_JSON}}", page)  # still the store's own text, not filled

    def test_render_without_data_says_so(self):
        self.assertEqual(mission.render("{{AS_OF}}", "{}\n", "{}\n", None), "n/a")


class Inline(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kit-mission-p-")
        self.addCleanup(tmp.cleanup)
        self.parts = Path(tmp.name) / "parts"
        write(self.parts, "a.html", "<a>\n")
        write(self.parts, "b.html", "<b>{{part:a.html}} v{{KIT_VERSION}}</b>\n")

    def test_a_part_is_inserted_once_and_its_own_part_tokens_stay(self):
        got = mission.inline("x {{part:a.html}} y {{part: b.html }} z", self.parts)
        self.assertEqual(got, f"x <a>\n y <b>{{{{part:a.html}}}} v{kit_version()}</b>\n z")

    def test_the_version_is_the_kits(self):
        self.assertEqual(mission.inline("v{{KIT_VERSION}}", None), f"v{kit_version()}")

    def test_a_part_that_cannot_be_used_names_itself(self):
        write(self.parts, "bad.html", "")
        (self.parts / "bad.html").write_bytes(b"\xff\xfe not utf-8")
        (self.parts / "dir.html").mkdir()
        for name in ("nope.html", "../escape", "sub/x.html", ".hidden", "", "bad.html", "dir.html"):
            with self.assertRaisesRegex(mission.Fail, r"^template part missing: parts/" + re.escape(name) + r"$", msg=name):
                mission.inline("{{part:%s}}" % name, self.parts)
        with self.assertRaisesRegex(mission.Fail, r"^template part missing: parts/a\.html$"):
            mission.inline("{{part:a.html}}", None)  # a template with no parts directory


class Source(unittest.TestCase):
    def test_the_script_is_executable_with_an_env_shebang(self):
        self.assertTrue(os.access(MISSION, os.X_OK))
        self.assertEqual(MISSION.read_text(encoding="utf-8").split("\n", 1)[0], "#!/usr/bin/env python3")

    def test_it_never_launches_a_browser_or_opens_a_connection(self):
        source = MISSION.read_text(encoding="utf-8")
        for word in ("webbrowser", "xdg-open", "startfile", "wslview", "explorer.exe"):
            self.assertNotIn(word, source)
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        network = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc",
                   "requests", "httpx", "webbrowser", "asyncio", "selectors", "socketserver"}
        self.assertEqual(imported & network, set())


# ── the leak gate ─────────────────────────────────────────────────────────────────────────────────────────────
class Gate(Store):
    """A secret-shaped value or an e-mail address anywhere in the data stops the build before anything is written."""

    CASES = (  # (section the value lands in, what the gate calls it, the plant)
        ("sessions", "GitHub token", lambda root: plant(
            root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: deploy with {TOKEN}")),
        ("pointers", "e-mail address", lambda root: plant(
            root, "memory/MEMORY.md", "run the full CI target before pushing", f"ask {EMAIL} first")),
        ("catalog", "GitHub token", lambda root: plant(
            root, "repos/repos-01.md", "title: Repos note 01", f"title: Repos {TOKEN} 01")),
        ("priorities", "GitHub token", lambda root: plant(
            root, "reference/priorities.md", "Ship the widget exporter", f"Ship {TOKEN} exporter")),
        ("cost", "e-mail address", lambda root: plant(root, "sessions/_ledger.md", "`kit-delta`", f"`{EMAIL}`")),
        ("queues", "GitHub token", lambda root: plant(
            root, "state/pr-review/ledger.jsonl", '"head": "abc1234"', f'"head": "{TOKEN}"')),
        # JSON writes a newline or a tab as a backslash and a letter: these begin a line, or follow a tab, in the real text
        ("sessions", "GitHub token", lambda root: plant(  # the start of the second line of a multi-line prompt
            root, "sessions/kit-delta.md", NEXT_PROMPT, f"Register as kit-delta-2 and continue the export.\n{TOKEN}")),
        ("sessions", "GitHub token", lambda root: plant(  # right after a tab
            root, "sessions/kit-delta.md", NEXT_PROMPT, f"Register as kit-delta-2, then:\t{TOKEN}")),
        ("sessions", "GitHub token", lambda root: plant(  # a newline-separated list where only the last line carries it
            root, "sessions/kit-delta.md", NEXT_PROMPT, f"- register as kit-delta-2\n- read the open PR list\n{TOKEN}")),
        ("sessions", "e-mail address", lambda root: plant(  # the second line begins with the address: reported whole
            root, "sessions/kit-delta.md", NEXT_PROMPT, f"Register as kit-delta-2.\n{EMAIL} has the open PR list")),
        ("queues", "e-mail address", lambda root: plant(  # the walk outcome: its heading, then the first line under it
            root, "kit-health/2026-10-09-walk.md", "Filed two follow-ups; nothing blocks the release.",
            f"{EMAIL} filed two follow-ups")),
    )

    def test_a_secret_or_an_address_in_any_section_stops_the_build(self):
        for section, what, put in self.CASES:
            with self.subTest(section=section):
                self.reset()
                put(self.root)
                r = self.build()
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                shown = "gh" if what == "GitHub token" else "so"  # the first two characters, never the value
                self.assertRegex(r.stdout, rf"\A{section} · line \d+ · {what} · {what}:{shown}…\n\Z")
                self.assertEqual(r.stderr, "mission: 1 leak hit(s) — nothing written\n")
                for secret in (TOKEN, EMAIL):
                    self.assertNotIn(secret, r.stdout + r.stderr)
                self.assertFalse(self.out.exists(), "a refused build must not even create the output directory")

    def test_every_hit_is_listed_by_section_and_counted(self):
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: deploy with {TOKEN}")
        plant(self.root, "memory/MEMORY.md", "run the full CI target before pushing", f"ask {EMAIL} first")
        r = self.build()
        self.assertEqual(r.returncode, 1)
        self.assertEqual([line.split(" · ")[0] for line in r.stdout.splitlines()], ["pointers", "sessions"])
        self.assertEqual(r.stderr, "mission: 2 leak hit(s) — nothing written\n")
        self.assertFalse(self.out.exists())

    def test_two_values_on_one_line_are_two_hits(self):
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: send {TOKEN} to {EMAIL}")
        r = self.build()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(sorted(line.split(" · ")[2] for line in r.stdout.splitlines()), ["GitHub token", "e-mail address"])
        self.assertEqual(r.stderr, "mission: 2 leak hit(s) — nothing written\n")

    def test_the_line_number_counts_the_lines_of_that_sections_own_json(self):
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: deploy with {TOKEN}")
        gated = self.build()
        self.assertEqual(gated.returncode, 1)
        line = int(re.match(r"sessions · line (\d+) · ", gated.stdout).group(1))
        self.reset()
        twin = "x" * len(TOKEN)  # the same store with a harmless value of the same length
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: deploy with {twin}")
        self.assertEqual(self.build().returncode, 0)
        lines = mission.dumps(self.data()["sessions"]).split("\n")
        self.assertIn(f"deploy with {twin}", lines[line - 1])

    def test_a_refused_build_leaves_an_earlier_one_untouched_and_force_does_not_bypass_it(self):
        self.assertEqual(self.build().returncode, 0)
        before = snapshot(self.out)
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: deploy with {TOKEN}")
        for args in ((), ("--force",)):
            r = self.build(*args)
            self.assertEqual(r.returncode, 1, args)
            self.assertEqual(snapshot(self.out), before, args)

    def test_a_workspace_path_is_allowed(self):
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export",
              "working_on: edit /home/user/work/widgets/app.py")
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.data()["sessions"]["active"][0]["working_on"], "edit /home/user/work/widgets/app.py")

    def test_text_that_only_looks_like_an_address_is_allowed(self):
        at = "@"  # put together at run time: the review gate reads e-mail-shaped text in source files
        for text in ("clone git@github.com:acme/x.git", "ask @claude to review", f"pin npm{at}10.2.4", "log in as user@host",
                     f"a{at}b.c"):
            with self.subTest(text=text):
                self.reset()
                plant(self.root, "reference/priorities.md", "Ship the widget exporter", text)
                r = self.build()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(text, [i["text"] for i in self.data()["priorities"]["items"]])

    def test_the_fixture_s_ssh_remote_title_is_built_not_flagged(self):
        self.assertEqual(self.build().returncode, 0)
        self.assertIn(SSH_TITLE, [d["title"] for d in self.data()["catalog"]["docs"]])


class GateText(unittest.TestCase):
    """mission.gate reads every string, keys included, as the page shows it: JSON writes a newline or a tab as a
    backslash and a letter, which hides a token that starts a line from the shapes' word boundary."""
    TOKEN_HIT = "sessions · line 2 · GitHub token · GitHub token:gh…"
    EMAIL_HIT = "sessions · line 2 · e-mail address · e-mail address:so…"

    def hits(self, value, key: str = "prompt") -> list:
        return mission.gate({"sessions": {key: value}})

    def test_a_token_that_starts_a_later_line_is_a_hit(self):
        self.assertEqual(self.hits(f"Resume.\n{TOKEN}"), [self.TOKEN_HIT])

    def test_a_token_after_any_character_json_escapes_is_a_hit(self):
        for before in ("\n", "\r\n", "\r", "\t", "\f", "\b", "\x0b", "\x1f", "\x00", '"', "\\"):
            with self.subTest(before=before):
                self.assertEqual(self.hits(f"x{before}{TOKEN}"), [self.TOKEN_HIT])

    def test_a_token_that_is_the_whole_value_or_sits_mid_line_is_still_a_hit(self):
        self.assertEqual(self.hits(TOKEN), [self.TOKEN_HIT])
        self.assertEqual(self.hits(f"token {TOKEN} here"), [self.TOKEN_HIT])

    def test_an_address_that_starts_a_later_line_is_reported_whole(self):
        for before in ("\n", "\t"):
            with self.subTest(before=before):
                self.assertEqual(self.hits(f"Resume.{before}{EMAIL}"), [self.EMAIL_HIT])  # not `ns…` or `ts…`

    def test_only_the_last_line_of_a_newline_separated_list_can_carry_it(self):
        self.assertEqual(self.hits("\n".join(["- read the open PR list", "- continue the export", TOKEN])), [self.TOKEN_HIT])
        self.assertEqual(self.hits("\n".join(["- read the open PR list", "- continue the export", EMAIL])), [self.EMAIL_HIT])

    def test_every_line_of_a_value_is_scanned_and_every_hit_listed(self):
        self.assertEqual(self.hits(f"{TOKEN}\nclean\n{EMAIL}"), [self.TOKEN_HIT, self.EMAIL_HIT])

    def test_a_key_is_scanned_like_a_value(self):
        self.assertEqual(self.hits(1, key=f"x\n{TOKEN}"), [self.TOKEN_HIT])
        self.assertEqual(self.hits(1, key=EMAIL), [self.EMAIL_HIT])

    def test_a_multi_line_value_is_reported_at_the_json_line_that_holds_it(self):
        found = mission.gate({"sessions": {"a": 1, "b": f"x\ny\n{TOKEN}", "c": "clean"}})
        self.assertEqual(found, ["sessions · line 3 · GitHub token · GitHub token:gh…"])
        self.assertIn("\\n", mission.dumps({"b": "x\ny"}))  # one JSON line, however many lines the value has

    def test_text_without_a_shape_has_no_hit_however_many_lines_it_has(self):
        text = "Resume.\n\tindented\n- git@github.com:acme/x.git\n\n\"quoted\" and a \\ backslash\n/home/user/work/app.py"
        self.assertEqual(self.hits(text), [])

    def test_numbers_flags_nulls_and_empty_containers_have_nothing_to_scan(self):
        self.assertEqual(mission.gate({"n": {"i": 1, "f": 1.5, "t": True, "z": None, "e": [], "d": {}}, "s": 1, "x": None}), [])


# ── the page template ─────────────────────────────────────────────────────────────────────────────────────────
class Templates(Store):
    def template(self, text: str, parts=None, name: str = "page.tpl.html") -> Path:
        path = write(self.tmp / "t", name, text)
        for part, body in (parts or {}).items():
            write(self.tmp / "t" / "parts", part, body)
        return path

    def test_tokens_parts_and_the_version_are_filled_and_the_data_round_trips(self):
        self.assertEqual(self.build().returncode, 0)
        page = self.text("index.html")
        self.assertEqual(json.loads(script_json(page, "data")), self.data())
        self.assertEqual(json.loads(script_json(page, "build")), self.build_json())
        self.assertEqual(page.count("<i>part-x</i>"), 1)
        self.assertIn(f"<p>kit {kit_version()}</p>", page)
        self.assertNotIn("{{", page)

    def test_store_text_cannot_end_a_script_block_or_reach_a_token(self):
        hostile = ("</script><b>x</b> <!-- {{part:x.html}} {{KIT_VERSION}} {{DATA_JSON}} {{BUILD_JSON}} "
                   "\\1 \\g<0> $&     end")
        plant(self.root, "memory/MEMORY.md", "run the full CI target before pushing", hostile)
        self.assertEqual(self.build().returncode, 0)
        page = self.text("index.html")
        for ident in ("data", "build"):
            self.assertNotIn("<", script_json(page, ident))
        self.assertEqual(page.count("</script>"), 2)  # the template's own two
        self.assertNotIn(" ", page)
        self.assertNotIn(" ", page)
        hook = json.loads(script_json(page, "data"))["pointers"]["memory"][1]["hook"]
        self.assertEqual(hook, hostile)  # round-trips, and nothing in it was filled in
        self.assertEqual(page.count("<i>part-x</i>"), 1)
        self.assertEqual(json.loads(self.text("data.json"))["pointers"]["memory"][1]["hook"], hostile)

    def test_a_missing_part_is_a_usage_error_that_writes_nothing(self):
        write(self.tmp / "t", "escape", "outside the parts directory")  # `../escape` would find it if it were allowed
        for name in ("nope.html", "../escape", "sub/x.html"):
            with self.subTest(part=name):
                r = self.build(template=self.template(f"<p>{{{{part:{name}}}}}</p>"))
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertEqual((r.stdout, r.stderr), ("", f"mission: template part missing: parts/{name}\n"))
                self.assertFalse(self.out.exists())

    def test_a_template_that_cannot_be_read_is_a_usage_error_that_writes_nothing(self):
        bad = self.tmp / "t" / "bad.tpl.html"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"\xff\xfe not utf-8")
        for path, why in ((self.tmp / "nope.tpl.html", "No such file or directory"), (self.tmp, "Is a directory"),
                          (bad, "not valid UTF-8")):
            with self.subTest(template=path.name):
                r = self.build(template=path)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertEqual((r.stdout, r.stderr), ("", f"mission: cannot read template {path.name}: {why}\n"))
                self.assertFalse(self.out.exists())

    def test_the_page_sha_follows_the_template_and_its_parts_but_the_data_sha_does_not(self):
        path = self.template("<p>one {{part:p.html}}</p>\n{{DATA_JSON}}\n", {"p.html": "first"})
        self.assertEqual(self.build(template=path).returncode, 0)
        first = self.build_json()
        write(self.tmp / "t", "page.tpl.html", "<p>two {{part:p.html}}</p>\n{{DATA_JSON}}\n")
        changed = self.diff(template=path)
        self.assertEqual(changed.returncode, 1)
        self.assertRegex(changed.stdout, r"^mission: changed [0-9a-f]{12} -> [0-9a-f]{12} ")
        self.assertEqual(self.build(template=path).returncode, 0)
        second = self.build_json()
        write(self.tmp / "t" / "parts", "p.html", "other")
        self.assertEqual(self.build(template=path).returncode, 0)
        third = self.build_json()
        self.assertEqual(len({first["page_sha"], second["page_sha"], third["page_sha"]}), 3)
        self.assertEqual({first["data_sha"], second["data_sha"], third["data_sha"]}, {first["data_sha"]})
        self.assertIn("<p>two other</p>", self.text("index.html"))

    def test_no_output_holds_a_path_of_this_machine(self):
        self.assertEqual(self.build().returncode, 0)
        for name in ("data.json", "build.json", "index.html", "last.sha", ".gitignore"):
            for local in (str(self.tmp), str(KIT), str(Path.home())):
                self.assertNotIn(local, self.text(name), name)


class KitCopy(Store):
    """mission.py as another kit sees it: a copy of the engine's bin/ under <tmp>/kit/context-db/bin with a VERSION of
    its own, so the kit's page template can be present or absent per test (the suite never reads the real one)."""

    BUILTIN_LABEL = "builtin"
    KIT_TEMPLATE = "skills/mission-control/page.tpl.html"

    def setUp(self):
        super().setUp()
        self.kit = self.tmp / "kit"
        shutil.copytree(BIN, self.kit / "context-db" / "bin", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        write(self.kit, "VERSION", "9.9.9\n")
        self.script = self.kit / "context-db" / "bin" / "mission.py"

    def mission(self, *args, **kw) -> subprocess.CompletedProcess:
        return run_mission(self.root, *args, script=self.script, **kw)

    def install_template(self, text: str = TINY, parts=None) -> None:
        write(self.kit, self.KIT_TEMPLATE, text)
        for part, body in (parts or {"x.html": PART_X}).items():
            write(self.kit, f"skills/mission-control/parts/{part}", body)

    def test_with_no_template_installed_the_builtin_page_is_built(self):
        r = self.build(template=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        build, page = self.build_json(), self.text("index.html")
        self.assertEqual((build["template"], build["kit_version"]), (self.BUILTIN_LABEL, "9.9.9"))
        self.assertEqual(json.loads(script_json(page, "data")), self.data())
        self.assertEqual(json.loads(script_json(page, "build")), build)
        self.assertNotRegex(page, r"\{\{[A-Z_]+\}\}")
        self.assertIn("9.9.9", page)
        self.assertIn(f"data as of {AS_OF}", page)
        self.assertEqual(page.count("</script>"), 2)

    def test_the_builtin_page_makes_no_request_and_loads_nothing(self):
        self.assertEqual(self.build(template=None).returncode, 0)
        page = self.text("index.html")
        markup = re.sub(r"<pre>.*?</pre>|<script\b.*?</script>", "", page, flags=re.S)  # the data blocks hold the store's URLs
        for source, label in ((markup, "the built page"), (mission.BUILTIN_PAGE, "the template")):
            for needle in ("http://", "https://", "<script src", "src=", "href=", "<link", "@import", "url(", "<iframe",
                           "<img", "<form", "<base"):
                self.assertNotIn(needle, source, f"{needle} in {label}")

    def test_the_installed_kit_template_is_used_with_its_parts(self):
        self.install_template()
        r = self.build(template=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        page = self.text("index.html")
        self.assertEqual(self.build_json()["template"], self.KIT_TEMPLATE)
        self.assertIn("<i>part-x</i>", page)
        self.assertIn("<p>kit 9.9.9</p>", page)
        self.assertEqual(json.loads(script_json(page, "data")), self.data())

    def test_an_explicit_template_wins_over_the_installed_one(self):
        self.install_template()
        other = write(self.tmp, "other/own.tpl.html", "<main>{{DATA_JSON}}</main>\n")
        self.assertEqual(self.build(template=other).returncode, 0)
        self.assertEqual(self.build_json()["template"], "own.tpl.html")
        self.assertNotIn("part-x", self.text("index.html"))

    def test_a_template_given_by_a_path_inside_the_kit_is_named_relative_to_it(self):
        self.install_template()
        self.assertEqual(self.build(template=self.kit / self.KIT_TEMPLATE).returncode, 0)
        self.assertEqual(self.build_json()["template"], self.KIT_TEMPLATE)

    def test_a_kit_upgrade_changes_the_page_and_so_the_build(self):
        self.assertEqual(self.build(template=None).returncode, 0)
        self.assertEqual(self.diff(template=None).returncode, 0)
        write(self.kit, "VERSION", "9.9.10\n")
        changed = self.diff(template=None)
        self.assertEqual(changed.returncode, 1)
        self.assertRegex(changed.stdout, r"^mission: changed ")
        self.assertEqual(self.build(template=None).returncode, 0)
        self.assertEqual(self.build_json()["kit_version"], "9.9.10")
        self.assertIn("9.9.10", self.text("index.html"))

    def test_installing_the_template_changes_the_page_too(self):
        self.assertEqual(self.build(template=None).returncode, 0)
        before = self.build_json()["page_sha"]
        self.install_template()
        self.assertEqual(self.diff(template=None).returncode, 1)
        self.assertEqual(self.build(template=None).returncode, 0)
        self.assertNotEqual(self.build_json()["page_sha"], before)
        self.assertEqual(self.build_json()["template"], self.KIT_TEMPLATE)


# ── the store's git head ──────────────────────────────────────────────────────────────────────────────────────
class StoreSha(Store):
    def commit(self, repo: Path) -> str:
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fixture")
        return git(repo, "rev-parse", "HEAD")

    def store_sha(self, *args):
        r = self.build(*args, env=self.git_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return self.build_json()["store_sha"]

    def test_a_store_that_is_not_a_repository_has_none(self):
        self.assertIsNone(self.store_sha())

    def test_a_committed_store_gives_its_head(self):
        head = self.commit(self.root)
        self.assertRegex(head, r"^[0-9a-f]{40}$")
        self.assertEqual(self.store_sha(), head)

    def test_the_head_follows_the_store(self):
        first = self.commit(self.root)
        write(self.root, "notes/notes-99.md", doc_text("Another note", "note", "notes", "active", "2026-09-30"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "second")
        second = git(self.root, "rev-parse", "HEAD")
        self.assertNotEqual(first, second)
        self.assertEqual(self.store_sha(), second)  # a changed doc is a changed page, so this build is a real one

    def test_a_repository_with_no_commit_has_none(self):
        git(self.root, "init", "-q")
        self.assertIsNone(self.store_sha())

    def test_sync_off_has_none(self):
        self.commit(self.root)
        r = run_tool(BIN / "kb.py", self.root, "config-set", "context.sync", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.store_sha())

    def test_a_store_inside_another_repository_has_none(self):
        self.commit(self.tmp)  # the store is a folder of this repository, not a repository of its own
        self.assertTrue((self.tmp / ".git").is_dir())
        self.assertIsNone(self.store_sha())

    def test_the_head_is_not_part_of_the_page(self):
        self.assertIsNone(self.store_sha())
        built = (self.text("data.json"), self.build_json()["page_sha"])
        head = self.commit(self.root)
        unchanged = self.build(env=self.git_env())  # a commit alone changes nothing the page shows
        self.assertEqual(unchanged.stdout.split()[:2], ["mission:", "unchanged"])
        self.assertIsNone(self.build_json()["store_sha"])
        self.assertEqual(self.store_sha("--force"), head)
        self.assertEqual((self.text("data.json"), self.build_json()["page_sha"]), built)


# ── exit codes and usage ──────────────────────────────────────────────────────────────────────────────────────
def own_lines(stderr: str) -> list:
    """stderr without the one line every engine script prints when its content root holds no env store."""
    return [ln for ln in stderr.splitlines() if not ln.startswith("kit_profile: no env store at ")]


class ExitCodes(Store):
    def test_help_works_from_any_directory_without_a_store(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        env = engine_env(self.tmp / "no-such-store" / ".context")
        for args in (["--help"], ["build", "--help"], ["diff", "--help"], ["open", "--help"], ["build", "-h"]):
            r = self.mission(*args, env=env, cwd=elsewhere)
            self.assertEqual(r.returncode, 0, (args, r.stderr))
            self.assertEqual(own_lines(r.stderr), [])
            self.assertTrue(r.stdout.startswith("usage: mission.py"), r.stdout)
        self.assertFalse((self.tmp / "no-such-store").exists())

    def test_help_names_no_value_of_this_environment(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        home = str(Path.home())
        values = [str(self.tmp), str(self.root), str(KIT), ZONE_NAME, TRACKER_TEMPLATE, "acme", *([home] if len(home) > 1 else [])]
        for args in (["--help"], ["build", "--help"], ["diff", "--help"], ["open", "--help"]):
            r = self.mission(*args, cwd=elsewhere)  # a configured store is right there: the text must not read it
            self.assertEqual(r.returncode, 0, r.stderr)
            for value in values:
                self.assertNotIn(value, r.stdout, (args, value))

    def test_help_describes_the_three_commands_and_the_output_location(self):
        top = self.mission("--help").stdout
        for word in ("build", "diff", "open", "data.json", "index.html", "state/mission-control", ".gitignore"):
            self.assertIn(word, top)
        self.assertIn("--force", self.mission("build", "--help").stdout)
        self.assertIn("--template", self.mission("diff", "--help").stdout)
        self.assertNotIn("--template", self.mission("open", "--help").stdout)

    def test_a_missing_command_or_a_bad_flag_is_a_usage_error(self):
        for args in ([], ["frobnicate"], ["build", "--nope"], ["build", "--out"], ["diff", "--force"], ["open", "--template", "x"]):
            r = self.mission(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertEqual(r.stdout, "", args)
            self.assertIn("error:", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_a_content_root_that_does_not_exist_is_a_usage_error_that_creates_nothing(self):
        gone = self.tmp / "gone" / ".context"
        for args in (["build"], ["diff"], ["build", "--out", str(self.out)], ["diff", "--template", str(TPL)]):
            r = run_mission(gone, *args, cwd=self.tmp)
            self.assertEqual(r.returncode, 2, args)
            self.assertEqual(r.stdout, "", args)
            self.assertEqual(len(own_lines(r.stderr)), 1, r.stderr)
            self.assertRegex(own_lines(r.stderr)[0], r"\Amission: content root not found: \S*gone/\.context\Z")
        self.assertFalse((self.tmp / "gone").exists())
        self.assertFalse(self.out.exists())

    def test_open_without_a_store_is_absent_not_an_error(self):
        r = run_mission(self.tmp / "gone" / ".context", "open", cwd=self.tmp)
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertEqual(len(own_lines(r.stderr)), 1, r.stderr)
        self.assertRegex(own_lines(r.stderr)[0], r"\Amission: nothing built at \S+/state/mission-control/index\.html — run build first\Z")

    def test_an_output_directory_that_cannot_be_made_is_an_io_error(self):
        blocker = write(self.tmp, "blocker", "a file, not a directory\n")
        for out in (blocker, blocker / "sub"):
            r = self.mission("build", "--out", out, "--template", TPL)
            self.assertEqual(r.returncode, 2, (out, r.stdout))
            self.assertEqual(r.stdout, "")
            self.assertEqual(len(r.stderr.splitlines()), 1, r.stderr)
            self.assertTrue(r.stderr.startswith("mission: "), r.stderr)
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(blocker.read_text(encoding="utf-8"), "a file, not a directory\n")

    def test_the_documented_exit_codes(self):
        self.assertEqual(self.diff().returncode, 1)  # never built
        self.assertEqual(self.open_page().returncode, 1)
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.build().returncode, 0)  # unchanged
        self.assertEqual(self.diff().returncode, 0)
        self.assertEqual(self.open_page().returncode, 0)
        plant(self.root, "sessions/kit-alpha.md", "working_on: plan the export", f"working_on: {TOKEN}")
        self.assertEqual(self.build().returncode, 1)  # gate
        self.assertEqual(self.diff().returncode, 1)  # changed
        self.assertEqual(self.mission("build", "--bogus").returncode, 2)


# ── the make targets ──────────────────────────────────────────────────────────────────────────────────────────
@unittest.skipUnless(shutil.which("make"), "make is not installed")
class MakeTargets(Store):
    def make(self, target: str, *args: str, dry: bool = False, env_extra=None) -> subprocess.CompletedProcess:
        env = hermetic_env(self.tmp)
        for name in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "CONTEXT_ROOT", "CONTEXT", "OUT", "FORCE", "TZ"):
            env.pop(name, None)
        env.update(env_extra or {})
        line = ["make", "--no-print-directory", "-C", str(ENGINE), *(["-n"] if dry else []), target, f"CONTEXT={self.root}", *args]
        return subprocess.run(line, capture_output=True, text=True, encoding="utf-8", timeout=120, env=env, cwd=str(self.tmp))

    def test_the_store_and_out_reach_the_command(self):
        out = str(self.out)
        for target, command in (("mission-build", "build"), ("mission-diff", "diff"), ("mission-open", "open")):
            r = self.make(target, f"OUT={out}", dry=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f'CONTEXT_ROOT="{self.root}"', r.stdout)
            self.assertIn(f"mission.py {command} --out '{out}'", r.stdout)
            self.assertNotIn("--force", r.stdout)

    def test_force_is_passed_by_build_only_and_only_when_set(self):
        self.assertIn("--force", self.make("mission-build", "FORCE=1", dry=True).stdout)
        for args in ((), ("FORCE=",)):
            self.assertNotIn("--force", self.make("mission-build", *args, dry=True).stdout, args)

    def test_without_out_the_command_uses_its_default(self):
        for target in ("mission-build", "mission-diff", "mission-open"):
            self.assertNotIn("--out", self.make(target, dry=True).stdout, target)

    def test_out_from_the_shell_environment_is_ignored(self):
        r = self.make("mission-build", dry=True, env_extra={"OUT": str(self.tmp / "stray"), "FORCE": ""})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("--out", r.stdout)
        self.assertNotIn("stray", r.stdout)

    def test_out_is_quoted_as_data(self):
        out = f"{self.tmp}/o u't $(x) `y` $$z"
        r = self.make("mission-build", f"OUT={out}", dry=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        quoted = "'" + out.replace("'", "'\\''") + "'"
        self.assertIn(f"mission.py build --out {quoted}", r.stdout)

    def test_the_targets_run_the_engine(self):
        out = self.tmp / "made"
        built = self.make("mission-build", f"OUT={out}")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        self.assertTrue(built.stdout.startswith(f"mission: built {out / 'index.html'} "), built.stdout)
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(OUTPUTS))
        self.assertEqual(self.make("mission-build", f"OUT={out}").stdout.split()[:2], ["mission:", "unchanged"])
        diff = self.make("mission-diff", f"OUT={out}")
        self.assertEqual((diff.returncode, diff.stdout.split()[:2]), (0, ["mission:", "unchanged"]))
        self.assertEqual(self.make("mission-open", f"OUT={out}").stdout, f"{out / 'index.html'}\n")
        before, page = snapshot(out), (out / "index.html").read_text(encoding="utf-8")
        forced = self.make("mission-build", f"OUT={out}", "FORCE=1")
        self.assertEqual(forced.stdout.split()[:2], ["mission:", "built"])
        after = snapshot(out)
        self.assertNotEqual(after, before)  # rewritten: new files
        for name in ("data.json", "last.sha", ".gitignore"):
            self.assertEqual(after[name][2], before[name][2], name)  # with the same bytes
        self.assertEqual(unstamped((out / "index.html").read_text(encoding="utf-8")), unstamped(page))

    def test_a_failing_command_fails_the_target(self):
        out = self.tmp / "never"
        r = self.make("mission-diff", f"OUT={out}")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, f"mission: not built {out}\n")

    def test_an_out_with_make_syntax_in_it_is_not_expanded(self):
        out = self.tmp / "q $(shell touch PWNED-m) 'z"
        r = self.make("mission-build", f"OUT={out}")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(OUTPUTS))
        self.assertEqual(list(self.tmp.rglob("PWNED*")) + list(ENGINE.glob("PWNED*")), [])

    def test_help_lists_the_targets(self):
        r = self.make("help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for target in ("mission-build", "mission-diff", "mission-open"):
            self.assertIn(target, r.stdout)
