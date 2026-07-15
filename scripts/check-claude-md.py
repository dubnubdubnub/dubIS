#!/usr/bin/env python
"""Guard: every repo path referenced in backticks in CLAUDE.md must exist.

CLAUDE.md is the first document every agent reads; a stale path sends agents
chasing files that no longer exist. Machine-generated docs have staleness
guards — this gives the hand-written one the same protection.

Usage: check-claude-md.py [--file CLAUDE.md] [--root .]
Exit 0 = all referenced paths exist; exit 1 = lists missing paths.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TOKEN_RE = re.compile(r"`([^`\n]+)`")
_PATHISH_RE = re.compile(r"^[A-Za-z0-9_\-./]+$")
_BARE_EXT_RE = re.compile(r"^\.[A-Za-z0-9]+$")
_SRC_EXTS = (".py", ".pyw", ".js", ".mjs", ".ts", ".css", ".html", ".json",
             ".sh", ".md", ".csv", ".yml", ".yaml")
_SKIP_PREFIXES = ("data/", "events/", "memory/", "~", "claude/")
_SKIP_EXACT = {
    ".mcp.json",  # gitignored local MCP config; documented but not checked in
}


def _is_checkable(token: str) -> bool:
    if not _PATHISH_RE.match(token):
        return False  # commands, flags, code snippets
    if _BARE_EXT_RE.match(token):
        return False  # bare extension mention (e.g. `.pyw`), not a path
    if token in _SKIP_EXACT:
        return False  # legitimately not present in the checkout
    if token.startswith(_SKIP_PREFIXES):
        return False  # runtime/user files or branch-name examples, not repo paths
    if "/" in token:
        return True
    return token.endswith(_SRC_EXTS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="CLAUDE.md")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    text = Path(args.file).read_text(encoding="utf-8")

    missing = []
    for token in _TOKEN_RE.findall(text):
        token = token.strip()
        if not _is_checkable(token):
            continue
        if not (root / token.rstrip("/")).exists():
            missing.append(token)

    if missing:
        print("CLAUDE.md references paths that do not exist:")
        for t in sorted(set(missing)):
            print(f"  {t}")
        print("Fix CLAUDE.md (or extend the skip rules in scripts/check-claude-md.py).")
        return 1
    print("check-claude-md: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
