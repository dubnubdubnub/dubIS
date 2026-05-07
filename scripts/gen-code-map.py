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
from dataclasses import dataclass, field
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


# ── Source tree walker ────────────────────────────────────────────────

# Directories whose contents are NOT part of our code map.
EXCLUDE_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    ".claude",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "test-results",
    "dist",
    "build",
    "openpnp",
    "tools",
    "docs",
    "data",
    "events",
    "css",
}


@dataclass
class FileInfo:
    path: str
    imports: list[str] = field(default_factory=list)
    emits: list[str] = field(default_factory=list)
    listens: list[str] = field(default_factory=list)


def _iter_source_files(root: Path) -> list[Path]:
    """Walk repo for .py and .js files, skipping EXCLUDE_DIRS at any depth."""
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".py", ".js"):
            continue
        # Skip if any ancestor directory name is in EXCLUDE_DIRS
        rel_parts = path.relative_to(root).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts[:-1]):
            continue
        found.append(path)
    return sorted(found)


def walk_sources(root: Path) -> dict[str, FileInfo]:
    """Return repo-relative path → FileInfo for every source file in `root`."""
    files = _iter_source_files(root)
    internal = {p.relative_to(root).as_posix() for p in files}

    info: dict[str, FileInfo] = {}
    for p in files:
        rel = p.relative_to(root).as_posix()
        fi = FileInfo(path=rel)
        if p.suffix == ".py":
            fi.imports = parse_python_imports(p, internal)
        else:  # .js
            fi.imports = parse_js_imports(p, root, internal)
            fi.emits, fi.listens = scan_eventbus_refs(p)
        info[rel] = fi
    return info


def _imported_by_index(info: dict[str, FileInfo]) -> dict[str, list[str]]:
    """For each file, the list of files that import it. Sorted."""
    rev: dict[str, list[str]] = {path: [] for path in info}
    for path, fi in info.items():
        for target in fi.imports:
            if target in rev:
                rev[target].append(path)
    for k in rev:
        rev[k] = sorted(rev[k])
    return rev


def _event_index(info: dict[str, FileInfo]) -> dict[str, dict[str, list[str]]]:
    """Event name -> {emitters: [...], listeners: [...]}, both sorted."""
    events: dict[str, dict[str, list[str]]] = {}
    for path, fi in info.items():
        for ev in fi.emits:
            events.setdefault(ev, {"emitters": [], "listeners": []})["emitters"].append(path)
        for ev in fi.listens:
            events.setdefault(ev, {"emitters": [], "listeners": []})["listeners"].append(path)
    for ev in events:
        events[ev]["emitters"] = sorted(set(events[ev]["emitters"]))
        events[ev]["listeners"] = sorted(set(events[ev]["listeners"]))
    return events


def render_markdown(info: dict[str, FileInfo]) -> str:
    """Render the full code-map document as Markdown."""
    lines: list[str] = []

    lines.append("# Code Map")
    lines.append("")
    lines.append("> Auto-generated by `scripts/gen-code-map.py`. Do not edit by hand.")
    lines.append("> CI fails if this file is stale; regenerate with `python scripts/gen-code-map.py`.")
    lines.append("")

    # ── Module imports (Mermaid graph) ─────────────────────────────
    lines.append("## Module imports")
    lines.append("")
    lines.append("```mermaid")
    lines.append("graph LR")
    edges: list[tuple[str, str]] = []
    for path in sorted(info):
        for target in info[path].imports:
            edges.append((path, target))
    if edges:
        for src, dst in edges:
            lines.append(f'    "{src}" --> "{dst}"')
    else:
        lines.append("    %% no internal imports detected")
    lines.append("```")
    lines.append("")

    # ── Event flow ────────────────────────────────────────────────
    lines.append("## Event flow")
    lines.append("")
    events = _event_index(info)
    if events:
        lines.append("| Event | Emitters | Listeners |")
        lines.append("|---|---|---|")
        for ev in sorted(events):
            emitters = ", ".join(f"`{e}`" for e in events[ev]["emitters"]) or "—"
            listeners = ", ".join(f"`{ll}`" for ll in events[ev]["listeners"]) or "—"
            lines.append(f"| `{ev}` | {emitters} | {listeners} |")
    else:
        lines.append("_No EventBus references found._")
    lines.append("")

    # ── Per-file index ────────────────────────────────────────────
    lines.append("## Per-file index")
    lines.append("")
    imported_by = _imported_by_index(info)
    for path in sorted(info):
        fi = info[path]
        lines.append(f"### {path}")
        lines.append("")
        lines.append(f"- **Imports:** {', '.join(f'`{p}`' for p in fi.imports) or '—'}")
        lines.append(f"- **Imported by:** {', '.join(f'`{p}`' for p in imported_by[path]) or '—'}")
        if fi.emits or fi.listens:
            lines.append(f"- **Emits:** {', '.join(f'`{e}`' for e in fi.emits) or '—'}")
            lines.append(f"- **Listens:** {', '.join(f'`{e}`' for e in fi.listens) or '—'}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit 1 if docs/code-map.md is stale")
    parser.parse_args(argv)
    # Implementation continues in later tasks
    return 0


if __name__ == "__main__":
    sys.exit(main())
