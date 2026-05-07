"""Generate docs/code-map.md from import + EventBus references in source files.

Walks Python (`.py`) and JS (`.js`) source files, parses internal imports and
`EventBus.on/.emit(Events.X)` references, and emits a Mermaid module graph plus
a per-file index. Mirrors the `--check` pattern from generate-test-fixtures.py.

Usage:
  python scripts/gen-code-map.py             # write docs/code-map.md
  python scripts/gen-code-map.py --check     # exit 1 if file is stale
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── Python import parsing ─────────────────────────────────────────────

def parse_python_imports(file_path: Path, internal_modules: set[str]) -> list[str]:
    """Return sorted unique relative paths (e.g. "domain/inventory.py") this
    Python file imports from the project. External imports are filtered out by
    membership in `internal_modules`.
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    found: set[str] = set()

    def _candidates_for(module_name: str) -> list[str]:
        # "foo.bar" -> ["foo/bar.py", "foo/bar/__init__.py"]
        parts = module_name.split(".")
        return [
            "/".join(parts) + ".py",
            "/".join(parts) + "/__init__.py",
        ]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for candidate in _candidates_for(alias.name):
                    if candidate in internal_modules:
                        found.add(candidate)
                        break
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue  # relative imports not used in this codebase
            for candidate in _candidates_for(node.module):
                if candidate in internal_modules:
                    found.add(candidate)
                    break

    return sorted(found)


# ── JS import parsing ─────────────────────────────────────────────────

# Matches: import ... from "..."   or   import "..."
# Captures the source string (group 1).
# Multiline-friendly: the {...} brace group can span lines.
_JS_IMPORT_RE = re.compile(
    r"""
    ^\s*import \s+
    (?:
        (?: \{ [^}]* \} )                 # named: { a, b }
        | (?: \* \s+ as \s+ \w+ )         # namespace: * as ns
        | (?: \w+ )                       # default: foo
        | (?: \w+ \s*,\s* \{ [^}]* \} )   # default + named: foo, { a }
    )
    \s+ from \s+ ['"]([^'"]+)['"]
    """,
    re.VERBOSE | re.MULTILINE | re.DOTALL,
)


def parse_js_imports(
    file_path: Path,
    repo_root: Path,
    internal_modules: set[str],
) -> list[str]:
    """Return sorted unique repo-relative paths this JS file imports from the
    project. Bare specifiers (e.g. 'lodash', '@scope/pkg') are skipped.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    # Strip block comments to avoid false matches.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Strip line comments.
    text = re.sub(r"//[^\n]*", "", text)

    found: set[str] = set()
    src_dir = file_path.parent

    for m in _JS_IMPORT_RE.finditer(text):
        spec = m.group(1)
        if not spec.startswith("."):
            continue  # bare specifier — external

        target = (src_dir / spec).resolve()
        try:
            rel = target.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            continue  # outside repo

        if rel in internal_modules:
            found.add(rel)

    return sorted(found)


# ── EventBus reference scanning ──────────────────────────────────────

_EVENTBUS_EMIT_RE  = re.compile(r"\bEventBus\.emit\(\s*Events\.([A-Z_][A-Z0-9_]*)")
_EVENTBUS_ON_RE    = re.compile(r"\bEventBus\.on\(\s*Events\.([A-Z_][A-Z0-9_]*)")


def scan_eventbus_refs(file_path: Path) -> tuple[list[str], list[str]]:
    """Return (emits, listens) — sorted unique Event names referenced in this
    JS file via `EventBus.emit(Events.X)` and `EventBus.on(Events.X)`.

    The Events enum definition in event-bus.js does not match these patterns,
    so it's naturally excluded.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [], []

    emits = sorted(set(_EVENTBUS_EMIT_RE.findall(text)))
    listens = sorted(set(_EVENTBUS_ON_RE.findall(text)))
    return emits, listens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit 1 if docs/code-map.md is stale")
    parser.parse_args(argv)
    # Implementation continues in later tasks
    return 0


if __name__ == "__main__":
    sys.exit(main())
