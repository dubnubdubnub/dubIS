#!/bin/bash
# Claude Code PostToolUse hook: regenerate JS test fixtures when backend Python changes.
# Reads JSON from stdin, checks if the edited file is a backend .py file,
# and silently regenerates fixtures if so.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

# Only trigger for Python files in the project root (not tests/, scripts/, tools/)
case "$FILE_PATH" in
  *.py)
    # Exclude non-backend paths
    case "$FILE_PATH" in
      */tests/*|*/scripts/*|*/tools/*) exit 0 ;;
    esac
    # Regenerate fixtures quietly
    python "$(dirname "$0")/generate-test-fixtures.py" > /dev/null 2>&1
    ;;
esac
exit 0
