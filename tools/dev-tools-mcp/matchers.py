"""Pure (stdlib-only) matchers for the devtools MCP tools.

No dependency on `mcp` / FastMCP — importable in tests without that package.
`server.py` delegates its api_callers and event_trace tools to these functions.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ── Filesystem helpers (duplicated from server.py to keep this stdlib-only) ──

_EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", "dist", "build", ".next", ".cache", "test-results",
}

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".svg",
    ".zip", ".tar", ".gz", ".7z", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".wav", ".avi", ".mov", ".db", ".sqlite", ".sqlite3",
}

_MAX_FILE_SIZE = 500_000  # 500 KB


def _walk_js_files(root: Path) -> list[Path]:
    """Walk *root* for *.js files, skipping excluded dirs and binaries."""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for f in filenames:
            if not f.endswith(".js"):
                continue
            fp = Path(dirpath) / f
            if fp.suffix.lower() in _BINARY_EXTENSIONS:
                continue
            try:
                if fp.stat().st_size > _MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            results.append(fp)
    return sorted(results)


def _safe_read(file_path: Path) -> list[str] | None:
    """Return lines of *file_path*, or None on decode/permission errors."""
    try:
        return file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (UnicodeDecodeError, PermissionError, OSError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────


def find_api_callers(
    method_name: str, js_root: Path, repo_root: Path
) -> list[dict]:
    """Return every JS line that calls a Python backend method.

    Matches all three calling conventions present in this codebase:

    1. ``api("method_name", ...)``          — string-keyed (dominant convention)
    2. ``api.method_name(...)``             — legacy direct dot-call
    3. ``pywebview.api.method_name(...)``   — direct bridge access

    Args:
        method_name: Python method name, e.g. ``"adjust_part"``.
        js_root:     Directory to walk (pass ``repo / "js"``).
        repo_root:   Repo root used to compute relative ``file`` paths.

    Returns:
        List of ``{"file": <repo-relative path>, "line": <1-based int>,
        "code": <stripped line>}``.  At most one entry per source line.
    """
    esc = re.escape(method_name)
    patterns = [
        # String-keyed convention: api("method_name", ...) or api('method_name', ...)
        re.compile(rf"""api\(\s*['"]{esc}['"]"""),
        # Legacy dot-call: api.method_name(
        re.compile(rf"api\.{esc}\s*\("),
        # Direct bridge access: pywebview.api.method_name(
        re.compile(rf"pywebview\.api\.{esc}\s*\("),
    ]

    results = []
    for file_path in _walk_js_files(js_root):
        lines = _safe_read(file_path)
        if lines is None:
            continue
        rel = str(file_path.relative_to(repo_root))
        for i, line in enumerate(lines):
            for pat in patterns:
                if pat.search(line):
                    results.append({
                        "file": rel,
                        "line": i + 1,
                        "code": line.strip(),
                    })
                    break  # at most one hit per line
    return results


def find_event_emitters_listeners(
    event_name: str, js_root: Path, repo_root: Path
) -> dict:
    """Return all JS emitters and listeners for an EventBus event.

    Accepts either the constant name (``"INVENTORY_UPDATED"``) or the string
    value (``"inventory-updated"``).

    Args:
        event_name: Event name — either the ``Events.X`` constant name or
                    its string value.
        js_root:    Directory to walk (pass ``repo / "js"``).
        repo_root:  Repo root used to compute relative ``file`` paths.

    Returns:
        ``{"event": event_name, "emitters": [...], "listeners": [...]}``
        where each entry is ``{"file": ..., "line": ..., "code": ...}``.
    """
    esc = re.escape(event_name)
    emit_patterns = [
        re.compile(rf"EventBus\.emit\(\s*Events\.{esc}"),
        re.compile(r'EventBus\.emit\(\s*["\']' + esc + r'["\']'),
    ]
    on_patterns = [
        re.compile(rf"EventBus\.on\(\s*Events\.{esc}"),
        re.compile(r'EventBus\.on\(\s*["\']' + esc + r'["\']'),
    ]

    emitters: list[dict] = []
    listeners: list[dict] = []

    for file_path in _walk_js_files(js_root):
        lines = _safe_read(file_path)
        if lines is None:
            continue
        rel = str(file_path.relative_to(repo_root))
        for i, line in enumerate(lines):
            for pat in emit_patterns:
                if pat.search(line):
                    emitters.append({
                        "file": rel,
                        "line": i + 1,
                        "code": line.strip(),
                    })
                    break
            for pat in on_patterns:
                if pat.search(line):
                    listeners.append({
                        "file": rel,
                        "line": i + 1,
                        "code": line.strip(),
                    })
                    break

    return {"event": event_name, "emitters": emitters, "listeners": listeners}
