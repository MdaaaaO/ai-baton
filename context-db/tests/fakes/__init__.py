"""Reusable fakes for scripts a context-db test spawns as a subprocess (#155) — currently just `gh`. Import
the module directly (`from tests.fakes import gh_stub`), never `import *`: a fake here has no side effect on
its own, it only writes a shim once asked."""
