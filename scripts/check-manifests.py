"""Verify that _README.md manifests accurately list the public exports of each
feature directory.

For each known directory, reads its _README.md, parses the "Public exports"
section, and verifies each claimed export exists in the source file.

Usage:
  python scripts/check-manifests.py          # exits 0 if all pass, 1 if any fail
  python scripts/check-manifests.py --check  # same (only mode we care about)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories to check (relative to REPO_ROOT)
MANIFEST_DIRS = [
    "js/inventory",
    "js/bom",
    "js/group-flyout",
    "js/import",
    "domain",
]


def parse_public_exports(readme_text: str) -> list[tuple[str, list[str]]]:
    """Parse the '## Public exports' section.

    Returns a list of (filename, [export_name, ...]) tuples.
    Lines outside the section are ignored.  A filename may appear on multiple
    lines (the section ends at the next '## ' heading or EOF).
    """
    # Find the Public exports section
    m = re.search(r"^## Public exports\s*\n(.*?)(?=^## |\Z)", readme_text,
                  re.MULTILINE | re.DOTALL)
    if not m:
        return []

    section = m.group(1)
    results: list[tuple[str, list[str]]] = []

    # Each non-empty line that starts with '- ' describes one file.
    # Format: `- \`<filename>\`: \`name1\`, \`name2\`, ... — description`
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue

        # Extract filename
        fm = re.search(r"`([^`]+\.(js|py))`\s*:", line)
        if not fm:
            continue
        filename = fm.group(1)

        # Extract backtick-quoted names that appear after the colon
        rest = line[fm.end():]
        names = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", rest)
        if names:
            results.append((filename, names))

    return results


def export_exists_js(file_path: Path, name: str) -> bool:
    """Return True if 'name' is exported from the JS file."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return False
    # Match: export function name / export const name / export var name /
    #        export class name / export { ..., name, ... } / export { name as ...}
    patterns = [
        rf"\bexport\s+(?:function|const|var|let|class|async\s+function)\s+{re.escape(name)}\b",
        rf"\bexport\s+\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}",
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    return False


def export_exists_py(file_path: Path, name: str) -> bool:
    """Return True if 'name' is defined at module level in the Python file."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return False
    patterns = [
        rf"^def {re.escape(name)}\b",
        rf"^class {re.escape(name)}\b",
        rf"^{re.escape(name)}\s*=",
    ]
    for p in patterns:
        if re.search(p, text, re.MULTILINE):
            return True
    return False


def check_dir(dirpath: Path) -> list[str]:
    """Return a list of error strings for this directory (empty = pass)."""
    readme = dirpath / "_README.md"
    if not readme.exists():
        return [f"{dirpath}/_README.md: missing"]

    text = readme.read_text(encoding="utf-8")
    claimed = parse_public_exports(text)
    if not claimed:
        return []  # no exports claimed — skip silently

    # Use a short label for error messages that works both in-repo and in tmp dirs
    try:
        readme_label = str(readme.relative_to(REPO_ROOT))
    except ValueError:
        readme_label = str(readme)

    errors: list[str] = []
    for filename, names in claimed:
        # Resolve relative to the directory under check
        source = dirpath / filename
        if not source.exists():
            errors.append(f"  {readme_label}: claims file '{filename}' "
                          "but file not found")
            continue

        is_py = filename.endswith(".py")
        for name in names:
            if is_py:
                found = export_exists_py(source, name)
            else:
                found = export_exists_js(source, name)
            if not found:
                errors.append(
                    f"  {readme_label}: claims export "
                    f"'{name}' in '{filename}' but not found"
                )

    return errors


def main() -> int:
    all_errors: list[str] = []

    for rel in MANIFEST_DIRS:
        dirpath = REPO_ROOT / rel
        if not dirpath.is_dir():
            all_errors.append(f"Directory not found: {rel}")
            continue
        errors = check_dir(dirpath)
        all_errors.extend(errors)

    if all_errors:
        print("check-manifests: FAILED")
        for e in all_errors:
            print(e)
        return 1

    print(f"check-manifests: OK ({len(MANIFEST_DIRS)} dirs checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
