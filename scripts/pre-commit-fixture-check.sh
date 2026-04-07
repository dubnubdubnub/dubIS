#!/bin/bash
# Git pre-commit hook: verify JS test fixtures are up-to-date.
# Install: ln -sf ../../scripts/pre-commit-fixture-check.sh .git/hooks/pre-commit
#
# Only checks when Python backend files are staged.

BACKEND_PY=$(git diff --cached --name-only --diff-filter=ACM | grep -E '^[^/]+\.py$' | grep -vE '^(app|dubis_headless)\.py$' || true)

if [ -n "$BACKEND_PY" ]; then
    python scripts/generate-test-fixtures.py --check > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo "ERROR: JS test fixtures are stale after Python changes."
        echo "Run: python scripts/generate-test-fixtures.py"
        echo "Then stage the updated fixtures: git add tests/fixtures/generated/"
        exit 1
    fi
fi
exit 0
