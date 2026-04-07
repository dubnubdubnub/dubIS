"""MCP dev-tools server — line_edit, multi_edit, symbol_search, block_grep, file_ops.

Efficiency tools for Claude Code that reduce token usage by eliminating
redundant reads, enabling batch operations, and providing smarter search.
"""

import fnmatch
import json
import os
import re
import shutil
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("devtools")

# ── Helpers ──────────────────────────────────────────────────

EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", "dist", "build", ".next", ".cache", "test-results",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".svg",
    ".zip", ".tar", ".gz", ".7z", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".wav", ".avi", ".mov", ".db", ".sqlite", ".sqlite3",
}

MAX_FILE_SIZE = 500_000  # 500KB


def _read_lines(file_path: str) -> list[str]:
    """Read file and return list of lines (preserving line endings)."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.readlines()


def _write_lines(file_path: str, lines: list[str]) -> None:
    """Write lines back to file."""
    with open(file_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)


def _project_root() -> Path:
    """Return the project root (cwd of the server process)."""
    return Path.cwd()


def _ensure_within_project(path: str) -> Path:
    """Resolve path and ensure it's within the project root."""
    resolved = Path(path).resolve()
    root = _project_root().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"Path {path} is outside project root {root}")
    return resolved


def _walk_files(root: Path, glob_pattern: str = "") -> list[Path]:
    """Walk directory tree, excluding common non-source directories."""
    if root.is_file():
        return [root]

    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]

        for f in filenames:
            fp = Path(dirpath) / f
            if fp.suffix.lower() in BINARY_EXTENSIONS:
                continue
            try:
                if fp.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            if glob_pattern and not fnmatch.fnmatch(f, glob_pattern):
                continue
            results.append(fp)

    return sorted(results)


def _safe_read(file_path: Path) -> list[str] | None:
    """Read a text file, returning None on decode/permission errors."""
    try:
        return file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (UnicodeDecodeError, PermissionError, OSError):
        return None


# ── Block extraction ─────────────────────────────────────────


def _find_block_end_brace(lines: list[str], start_idx: int) -> int:
    """Find end of a brace-delimited block starting near start_idx."""
    depth = 0
    found_open = False
    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return i
    return min(start_idx + 50, len(lines) - 1)


def _find_block_end_indent(lines: list[str], start_idx: int) -> int:
    """Find end of an indentation-based block (Python)."""
    if start_idx >= len(lines):
        return start_idx

    def_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())

    last_content_line = start_idx
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        current_indent = len(lines[i]) - len(lines[i].lstrip())
        if current_indent <= def_indent:
            break
        last_content_line = i

    return last_content_line


def _extract_block(lines: list[str], start_idx: int, ext: str) -> tuple[int, int]:
    """Extract the full block starting at start_idx. Returns (start, end) indices."""
    if ext == ".py":
        end = _find_block_end_indent(lines, start_idx)
    else:
        end = _find_block_end_brace(lines, start_idx)
    return start_idx, end


# ── line_edit ────────────────────────────────────────────────


@mcp.tool()
def line_edit(file_path: str, start_line: int, end_line: int, new_content: str) -> str:
    """Replace lines start_line through end_line (inclusive, 1-indexed) with new_content.

    Use instead of the built-in Edit tool when you already know the line numbers
    (e.g. from Grep or Read output). No need to copy exact old text.

    Args:
        file_path: Absolute path to the file
        start_line: First line to replace (1-indexed)
        end_line: Last line to replace (1-indexed, inclusive)
        new_content: Replacement text (include trailing newline if needed)

    Returns:
        JSON with old_content, lines replaced, and new total line count
    """
    lines = _read_lines(file_path)

    if start_line < 1 or end_line < start_line or start_line > len(lines):
        raise ValueError(
            f"Invalid line range {start_line}-{end_line} "
            f"for file with {len(lines)} lines"
        )

    end_line = min(end_line, len(lines))
    old_content = "".join(lines[start_line - 1 : end_line])

    # Handle deletion (empty new_content)
    if not new_content:
        new_lines: list[str] = []
    else:
        if not new_content.endswith("\n"):
            new_content += "\n"
        new_lines = new_content.splitlines(keepends=True)

    lines[start_line - 1 : end_line] = new_lines

    _write_lines(file_path, lines)

    return json.dumps({
        "ok": True,
        "file": file_path,
        "replaced_lines": f"{start_line}-{end_line}",
        "old_content": old_content,
        "new_line_count": len(new_lines),
        "total_lines": len(lines),
    })


# ── multi_edit ───────────────────────────────────────────────


@mcp.tool()
def multi_edit(file_path: str, edits: list[dict]) -> str:
    """Apply multiple line-range edits to a single file in one call.

    Edits are applied in reverse line order automatically so line numbers
    stay valid. Each edit: {"start_line": N, "end_line": N, "new_content": "..."}.

    Args:
        file_path: Absolute path to the file
        edits: List of edits, each with start_line, end_line (1-indexed inclusive), new_content

    Returns:
        JSON with number of edits applied and new total line count
    """
    lines = _read_lines(file_path)

    sorted_edits = sorted(edits, key=lambda e: e["start_line"], reverse=True)

    # Validate no overlaps
    for i in range(len(sorted_edits) - 1):
        if sorted_edits[i]["start_line"] <= sorted_edits[i + 1]["end_line"]:
            raise ValueError(
                f"Overlapping edit ranges: "
                f"{sorted_edits[i + 1]['start_line']}-{sorted_edits[i + 1]['end_line']} "
                f"and {sorted_edits[i]['start_line']}-{sorted_edits[i]['end_line']}"
            )

    for edit in sorted_edits:
        start = edit["start_line"]
        end = min(edit["end_line"], len(lines))
        content = edit["new_content"]

        if start < 1 or start > len(lines):
            raise ValueError(f"Invalid start_line {start}")

        if not content:
            new_lines: list[str] = []
        else:
            if not content.endswith("\n"):
                content += "\n"
            new_lines = content.splitlines(keepends=True)

        lines[start - 1 : end] = new_lines

    _write_lines(file_path, lines)

    return json.dumps({
        "ok": True,
        "file": file_path,
        "edits_applied": len(sorted_edits),
        "total_lines": len(lines),
    })


# ── symbol_search ────────────────────────────────────────────

SYMBOL_PATTERNS = {
    ".py": [
        r"^\s*(async\s+)?def\s+{name}\s*\(",
        r"^\s*class\s+{name}\s*[:\(]",
    ],
    ".js": [
        r"^\s*(export\s+)?(async\s+)?function\s+{name}\s*\(",
        r"^\s*(export\s+)?class\s+{name}\s*[\{{\s]",
        r"^\s*(export\s+)?(const|let|var)\s+{name}\s*=",
        r"^\s*{name}\s*\([^)]*\)\s*\{{",
    ],
    ".ts": [
        r"^\s*(export\s+)?(async\s+)?function\s+{name}\s*[\(<]",
        r"^\s*(export\s+)?class\s+{name}\s*[\{{\s<]",
        r"^\s*(export\s+)?(const|let|var)\s+{name}\s*[=:<]",
        r"^\s*(export\s+)?interface\s+{name}\s*[\{{\s<]",
        r"^\s*{name}\s*\([^)]*\)\s*[:\{{]",
    ],
}

SYMBOL_PATTERNS_GENERIC = [
    r"^\s*(export\s+)?(async\s+)?function\s+{name}\s*\(",
    r"^\s*(async\s+)?def\s+{name}\s*\(",
    r"^\s*class\s+{name}\s*[\(:\{{<]",
    r"^\s*(export\s+)?(const|let|var)\s+{name}\s*=",
]


@mcp.tool()
def symbol_search(name: str, path: str = ".", glob_pattern: str = "") -> str:
    """Find definitions of a function, class, method, or variable by name.

    Searches for common definition patterns (def, function, class, const, etc.)
    and returns the full body of each match. Much faster than grep for finding
    where something is defined.

    Args:
        name: Symbol name to search for (exact, not regex)
        path: Directory or file to search in (default: project root)
        glob_pattern: Filter files (e.g. "*.py", "*.js")

    Returns:
        JSON array of {file, line, end_line, body}
    """
    root = (
        Path(path).resolve()
        if os.path.isabs(path)
        else (_project_root() / path).resolve()
    )

    files = _walk_files(root, glob_pattern)
    results = []

    for file_path in files:
        ext = file_path.suffix.lower()
        patterns = SYMBOL_PATTERNS.get(ext, SYMBOL_PATTERNS_GENERIC)

        lines = _safe_read(file_path)
        if lines is None:
            continue

        for pat_template in patterns:
            pat = pat_template.format(name=re.escape(name))
            regex = re.compile(pat)

            for i, line in enumerate(lines):
                if regex.match(line):
                    start, end = _extract_block(lines, i, ext)
                    body = "".join(lines[start : end + 1])
                    results.append({
                        "file": str(file_path),
                        "line": i + 1,
                        "end_line": end + 1,
                        "body": body,
                    })

    # Deduplicate by file+line
    seen: set[tuple[str, int]] = set()
    unique = []
    for r in results:
        key = (r["file"], r["line"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return json.dumps(unique, indent=2)


# ── block_grep ───────────────────────────────────────────────

# Patterns that indicate a block start (function, class, method, etc.)
_BLOCK_DEF_PATTERNS = [
    r"^\s*(async\s+)?def\s+\w+",
    r"^\s*(export\s+)?(async\s+)?function\s+\w+",
    r"^\s*(export\s+)?class\s+\w+",
    r"^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s+)?[\(\[]",
    r"^\s*\w+\s*\([^)]*\)\s*\{",
    r"^\s*(export\s+)?interface\s+\w+",
]


def _find_enclosing_block(
    lines: list[str], match_idx: int, ext: str
) -> tuple[int, int]:
    """Find the enclosing function/class/block for a given line index."""
    for i in range(match_idx, -1, -1):
        for pat in _BLOCK_DEF_PATTERNS:
            if re.match(pat, lines[i]):
                start, end = _extract_block(lines, i, ext)
                if end >= match_idx:
                    return start, end

    # No enclosing block found — return context around match
    start = max(0, match_idx - 5)
    end = min(len(lines) - 1, match_idx + 20)
    return start, end


@mcp.tool()
def block_grep(
    pattern: str,
    path: str = ".",
    glob_pattern: str = "",
    max_results: int = 10,
) -> str:
    """Search for a regex pattern and return the full enclosing function/block.

    Like grep, but returns the entire function/class/block containing the match
    instead of just the matching line. Eliminates the grep-then-read round trip.

    Args:
        pattern: Regex pattern to search for
        path: Directory or file (default: project root)
        glob_pattern: Filter files (e.g. "*.js", "*.py")
        max_results: Max results to return (default: 10)

    Returns:
        JSON array of {file, match_line, block_start, block_end, body}
    """
    root = (
        Path(path).resolve()
        if os.path.isabs(path)
        else (_project_root() / path).resolve()
    )
    regex = re.compile(pattern)
    results = []
    seen_blocks: set[tuple[str, int]] = set()

    files = _walk_files(root, glob_pattern)

    for file_path in files:
        ext = file_path.suffix.lower()

        lines = _safe_read(file_path)
        if lines is None:
            continue

        for i, line in enumerate(lines):
            if regex.search(line):
                start, end = _find_enclosing_block(lines, i, ext)

                # Deduplicate blocks (multiple matches in same block)
                block_key = (str(file_path), start)
                if block_key in seen_blocks:
                    continue
                seen_blocks.add(block_key)

                body = "".join(lines[start : end + 1])
                results.append({
                    "file": str(file_path),
                    "match_line": i + 1,
                    "block_start": start + 1,
                    "block_end": end + 1,
                    "body": body,
                })
                if len(results) >= max_results:
                    return json.dumps(results, indent=2)

    return json.dumps(results, indent=2)


# ── file_ops ─────────────────────────────────────────────────


@mcp.tool()
def file_ops(operation: str, path: str, destination: str = "") -> str:
    """Perform file operations: mkdir, mv, cp, rm.

    All paths must be within the project root for safety.

    Args:
        operation: One of 'mkdir', 'mv', 'cp', 'rm'
        path: Source path (or directory for mkdir)
        destination: Destination path (required for mv and cp)

    Returns:
        JSON with operation result
    """
    resolved = _ensure_within_project(path)

    if operation == "mkdir":
        resolved.mkdir(parents=True, exist_ok=True)
        return json.dumps({"ok": True, "op": "mkdir", "path": str(resolved)})

    elif operation == "rm":
        if not resolved.exists():
            raise FileNotFoundError(f"{path} does not exist")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        return json.dumps({"ok": True, "op": "rm", "path": str(resolved)})

    elif operation in ("mv", "cp"):
        if not destination:
            raise ValueError(f"destination required for {operation}")
        dest = _ensure_within_project(destination)

        if operation == "mv":
            shutil.move(str(resolved), str(dest))
        else:
            if resolved.is_dir():
                shutil.copytree(str(resolved), str(dest))
            else:
                shutil.copy2(str(resolved), str(dest))

        return json.dumps({
            "ok": True,
            "op": operation,
            "source": str(resolved),
            "destination": str(dest),
        })

    else:
        raise ValueError(f"Unknown operation: {operation}. Use mkdir, mv, cp, or rm.")


# ── event_trace ──────────────────────────────────────────────


@mcp.tool()
def event_trace(event_name: str) -> str:
    """Trace an EventBus event: find all emitters and listeners.

    Given an event name (e.g. "INVENTORY_UPDATED" or "inventory-updated"),
    returns all files that emit and all files that subscribe to it.

    Args:
        event_name: Event name — either the Events.X constant name
                    or the string value (e.g. "inventory-updated")

    Returns:
        JSON with emitters and listeners arrays
    """
    root = _project_root() / "js"
    files = _walk_files(root, "*.js")

    # Normalize: accept either "INVENTORY_UPDATED" or "inventory-updated"
    emit_patterns = [
        re.compile(rf"EventBus\.emit\(\s*Events\.{re.escape(event_name)}"),
        re.compile(r'EventBus\.emit\(\s*["\']' + re.escape(event_name) + r"[\"']"),
    ]
    on_patterns = [
        re.compile(rf"EventBus\.on\(\s*Events\.{re.escape(event_name)}"),
        re.compile(r'EventBus\.on\(\s*["\']' + re.escape(event_name) + r"[\"']"),
    ]

    emitters = []
    listeners = []

    for file_path in files:
        lines = _safe_read(file_path)
        if lines is None:
            continue
        rel = str(file_path.relative_to(_project_root()))
        for i, line in enumerate(lines):
            for pat in emit_patterns:
                if pat.search(line):
                    emitters.append({
                        "file": rel,
                        "line": i + 1,
                        "code": line.strip(),
                    })
            for pat in on_patterns:
                if pat.search(line):
                    listeners.append({
                        "file": rel,
                        "line": i + 1,
                        "code": line.strip(),
                    })

    return json.dumps({"event": event_name, "emitters": emitters,
                        "listeners": listeners}, indent=2)


# ── api_callers ──────────────────────────────────────────────


@mcp.tool()
def api_callers(method_name: str) -> str:
    """Find all JS call sites for a Python API method.

    Searches for window.pywebview.api.<method> and api.<method> calls
    across the JS codebase to show where a Python backend method is used.

    Args:
        method_name: Python method name (e.g. "adjust_part", "rebuild_inventory")

    Returns:
        JSON array of {file, line, code} for each call site
    """
    root = _project_root() / "js"
    files = _walk_files(root, "*.js")

    patterns = [
        re.compile(rf"api\.{re.escape(method_name)}\s*\("),
        re.compile(rf"pywebview\.api\.{re.escape(method_name)}\s*\("),
    ]

    results = []
    for file_path in files:
        lines = _safe_read(file_path)
        if lines is None:
            continue
        rel = str(file_path.relative_to(_project_root()))
        for i, line in enumerate(lines):
            for pat in patterns:
                if pat.search(line):
                    results.append({
                        "file": rel,
                        "line": i + 1,
                        "code": line.strip(),
                    })
                    break  # one match per line is enough

    return json.dumps(results, indent=2)


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
