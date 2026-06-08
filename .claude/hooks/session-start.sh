#!/bin/bash
# SessionStart hook: install dependencies so ruff, the test suite and the
# scripts/ smoke checks all work in a fresh Claude Code on the web session.
set -euo pipefail

# Local machines already have their own environment; only set up the web sandbox.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# requirements-dev.txt pulls in runtime deps (-r requirements.txt) plus ruff and
# pytest, so this single install readies linting, tests, and the smoke scripts.
python -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt

echo "session-start: dependencies installed (ruff + pytest + runtime)."
