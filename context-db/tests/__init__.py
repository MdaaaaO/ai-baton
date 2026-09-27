"""The test package. Importing it scrubs the variables a Claude Code session exports into its Bash commands, so
`make test` gives the same result inside a plugin-install session as in CI (#20): the SessionStart hook exports
CLAUDE_PROJECT_DIR, BATON and WORKSPACE_* (kit_profile.py session-env), and Claude Code sets CLAUDE_PLUGIN_ROOT,
CLAUDE_ENV_FILE and CLAUDE_PLUGIN_OPTION_* for hooks. Tests that need one set it themselves. CONTEXT_ROOT stays:
the Makefile's `test` target sets it on purpose."""
import os

SESSION_VARS = ("CLAUDE_PROJECT_DIR", "BATON", "CLAUDE_PLUGIN_ROOT", "CLAUDE_ENV_FILE")
SESSION_PREFIXES = ("WORKSPACE_", "CLAUDE_PLUGIN_OPTION_")

for _k in [k for k in os.environ if k in SESSION_VARS or k.startswith(SESSION_PREFIXES)]:
    del os.environ[_k]
