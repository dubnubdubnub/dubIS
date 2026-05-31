"""Guard against new hard-coded px layout values in CSS and JS files.

CSS check: walks the scoped CSS files (panels, buttons, tables, components,
layout, modals) and reports any layout property declaration of the form
`<prop>: <N>px` where <prop> is a known layout-controlling property and <N>
is large enough to be a meaningful dimension (> 2px).

JS check (soft, conservative): scans JS files (excluding layout-tokens.js)
for numeric literals ≥ 3 assigned to layout-hinted variable names
(*_W, *_H, *_GAP, *_PADDING, *_PAD) that are NOT reading from layout-tokens.

Both checks respect an ignore list at scripts/check-layout-tokens.ignore,
where each line is a substring of "<file>:<lineno>:<text>" that marks a
known-accepted hard-code.

Usage:
    python scripts/check-layout-tokens.py           # print findings, exit 0 always
    python scripts/check-layout-tokens.py --check   # exit 1 if any disallowed match
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]

# CSS files to scan (globs resolved from REPO_ROOT)
CSS_GLOBS = [
    "css/panels/*.css",
    "css/buttons.css",
    "css/tables.css",
    "css/components/*.css",
    "css/layout.css",
    "css/modals.css",
]

# Layout-controlling CSS properties that should use tokens
LAYOUT_PROPS = {
    "width", "min-width", "max-width",
    "height", "min-height", "max-height",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "gap", "row-gap", "column-gap",
    "top", "bottom", "left", "right",
}

# Properties that allow tiny values > 0 and ≤ 2px are already handled by
# the TINY_OFFSET_MAX threshold below.  Additionally, border-related properties
# are unconditionally allowed because 1px borders are a universal CSS pattern.
BORDER_PROPS = {
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-width", "outline", "outline-width", "outline-offset",
    "border-radius", "border-top-left-radius", "border-top-right-radius",
    "border-bottom-left-radius", "border-bottom-right-radius",
}

# Px values ≤ this threshold are allowed (visual nudges, hairline adjustments)
TINY_OFFSET_MAX = 2

# JS files to exclude from the JS heuristic check
JS_EXCLUDE = {"layout-tokens.js"}

# Pattern for JS layout-hinted variable names (conservative — only all-caps
# names that end with a layout suffix are flagged)
JS_LAYOUT_VAR_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:_W|_H|_GAP|_PADDING|_PAD))\s*=\s*(\d+)\b"
)

IGNORE_FILE = REPO_ROOT / "scripts" / "check-layout-tokens.ignore"


# ── Ignore list loading ──────────────────────────────────────────────────────

def load_ignore_list() -> list[str]:
    """Return non-empty, non-comment lines from the ignore file."""
    if not IGNORE_FILE.exists():
        return []
    lines = IGNORE_FILE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def is_ignored(entry: str, ignore_list: list[str]) -> bool:
    """Return True if any ignore pattern is a substring of the entry string."""
    return any(pattern in entry for pattern in ignore_list)


# ── CSS scanning ─────────────────────────────────────────────────────────────

# Matches a property declaration: captures (property, value-before-px, semicolon)
# Works on a single stripped line. We look for `prop: ... <N>px` patterns.
_CSS_DECL_RE = re.compile(
    r"(?P<prop>[\w-]+)\s*:\s*(?P<value>[^;{]+)",
    re.IGNORECASE,
)

_PX_VALUE_RE = re.compile(r"\b(\d+)px\b")


def _is_layout_prop(prop: str) -> bool:
    return prop.lower() in LAYOUT_PROPS


def _is_border_prop(prop: str) -> bool:
    return prop.lower() in BORDER_PROPS


def _make_entry_label(path: Path, root: Path) -> str:
    """Return a relative posix path label for use in violation entries."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        # path is outside root (e.g. in tmp_path during tests) — use absolute
        return path.as_posix()


def _scan_css_file(
    path: Path, ignore_list: list[str], root: Path | None = None
) -> list[str]:
    """Return a list of violation strings for the given CSS file."""
    effective_root = root if root is not None else REPO_ROOT
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    inside_media = False
    media_depth = 0

    for lineno, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()

        # Track @media blocks — anything inside is exempt (responsive breakpoints)
        if "@media" in stripped:
            inside_media = True
        if inside_media:
            media_depth += raw_line.count("{")
            media_depth -= raw_line.count("}")
            if media_depth <= 0:
                inside_media = False
                media_depth = 0
            continue

        # Only look at lines that contain `px`
        if "px" not in stripped:
            continue

        # Skip pure comments
        if stripped.startswith("/*") or stripped.startswith("//"):
            continue

        # Strip inline comment portion before checking
        line_no_comment = re.sub(r"/\*.*?\*/", "", stripped)

        m = _CSS_DECL_RE.search(line_no_comment)
        if not m:
            continue

        prop = m.group("prop").lower()
        value_str = m.group("value")

        # Skip border/outline properties — 1px borders are universal
        if _is_border_prop(prop):
            continue

        # Skip non-layout properties
        if not _is_layout_prop(prop):
            continue

        # Find all Npx occurrences in the value
        for px_match in _PX_VALUE_RE.finditer(value_str):
            n = int(px_match.group(1))

            # Zero values are fine (margin: 0, padding: 0, etc.)
            if n == 0:
                continue

            # Tiny offset allowlist (≤ 2px — visual nudges)
            if n <= TINY_OFFSET_MAX:
                continue

            # Check for var() usage — if value already uses a token, skip
            if "var(" in value_str:
                continue

            # Build a concise entry string for ignore-list matching
            rel = _make_entry_label(path, effective_root)
            entry = f"{rel}:{lineno}:{stripped}"
            if is_ignored(entry, ignore_list):
                continue

            violations.append(entry)
            break  # one violation per line is enough

    return violations


def scan_css(ignore_list: list[str]) -> list[str]:
    """Scan all configured CSS files and return violation strings."""
    violations: list[str] = []
    for glob_pattern in CSS_GLOBS:
        for css_file in sorted(REPO_ROOT.glob(glob_pattern)):
            violations.extend(_scan_css_file(css_file, ignore_list, root=REPO_ROOT))
    return violations


# ── JS scanning (soft heuristic) ─────────────────────────────────────────────

def _scan_js_file(
    path: Path, ignore_list: list[str], root: Path | None = None
) -> list[str]:
    """Return heuristic JS violation strings."""
    effective_root = root if root is not None else REPO_ROOT
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for lineno, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for m in JS_LAYOUT_VAR_RE.finditer(raw_line):
            value = int(m.group(2))
            if value <= TINY_OFFSET_MAX:
                continue
            # If the assignment uses getLayoutTokenPx or similar, skip
            if "getLayoutToken" in raw_line or "var(--" in raw_line:
                continue
            rel = _make_entry_label(path, effective_root)
            entry = f"{rel}:{lineno}:{stripped}"
            if is_ignored(entry, ignore_list):
                continue
            violations.append(entry)
    return violations


def scan_js(ignore_list: list[str]) -> list[str]:
    """Scan JS files for layout-hinted hard-coded values."""
    violations: list[str] = []
    js_dir = REPO_ROOT / "js"
    if not js_dir.is_dir():
        return violations
    for js_file in sorted(js_dir.rglob("*.js")):
        if js_file.name in JS_EXCLUDE:
            continue
        violations.extend(_scan_js_file(js_file, ignore_list, root=REPO_ROOT))
    return violations


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any disallowed hard-coded px values are found",
    )
    args = parser.parse_args(argv)

    ignore_list = load_ignore_list()

    css_violations = scan_css(ignore_list)
    js_violations = scan_js(ignore_list)
    all_violations = css_violations + js_violations

    if all_violations:
        print("check-layout-tokens: VIOLATIONS FOUND")
        for v in all_violations:
            print(f"  {v}")
        if args.check:
            return 1
    else:
        total_ignored = len(ignore_list)
        print(
            f"check-layout-tokens: OK "
            f"({total_ignored} ignore-list entries, no new violations)"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
