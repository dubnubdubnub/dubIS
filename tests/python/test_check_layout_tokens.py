"""Unit tests for scripts/check-layout-tokens.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ── Load the script as a module ─────────────────────────────────────────────

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "check-layout-tokens.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_layout_tokens", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


clt = _load_module()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _write_css(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _write_js(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ── CSS: raw px violations ───────────────────────────────────────────────────

def test_css_detects_raw_width_px(tmp_path: Path) -> None:
    """A hard-coded width in px should be reported."""
    f = _write_css(tmp_path, "test.css", ".foo { width: 120px; }\n")
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert len(violations) == 1
    assert "width: 120px" in violations[0]


def test_css_detects_raw_padding_px(tmp_path: Path) -> None:
    """A hard-coded padding with multiple px values reports a violation."""
    f = _write_css(tmp_path, "test.css", ".bar { padding: 8px 12px; }\n")
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert len(violations) == 1


def test_css_accepts_var_token(tmp_path: Path) -> None:
    """A width using var(--token) must NOT be flagged."""
    f = _write_css(tmp_path, "test.css", ".foo { width: var(--inv-col-pn-w); }\n")
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_css_accepts_border_px(tmp_path: Path) -> None:
    """Border declarations with px values are unconditionally allowed."""
    f = _write_css(
        tmp_path, "test.css",
        ".foo { border: 1px solid red; border-width: 2px; }\n"
    )
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_css_accepts_tiny_offset(tmp_path: Path) -> None:
    """Values <= 2px are treated as visual-nudge offsets and allowed."""
    f = _write_css(tmp_path, "test.css", ".foo { top: 2px; margin-top: 1px; }\n")
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_css_accepts_zero_value(tmp_path: Path) -> None:
    """Zero px values are allowed (margin: 0, padding: 0, etc.)."""
    f = _write_css(tmp_path, "test.css", ".foo { margin: 0px; padding: 0; }\n")
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_css_accepts_media_query(tmp_path: Path) -> None:
    """Hard-coded px values inside @media blocks are exempt."""
    content = (
        "@media (max-width: 768px) {\n"
        "  .panel { width: 100%; min-width: 300px; }\n"
        "}\n"
    )
    f = _write_css(tmp_path, "test.css", content)
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_css_respects_ignore_list(tmp_path: Path) -> None:
    """An entry matching the ignore list is suppressed."""
    f = _write_css(tmp_path, "test.css", ".foo { width: 120px; }\n")
    # Build an ignore pattern that matches any part of the violation entry
    violations_before = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations_before  # sanity check: there's a violation
    # Use a substring of the violation line text as the pattern
    pattern = "width: 120px"
    violations_after = clt._scan_css_file(f, ignore_list=[pattern], root=tmp_path)
    assert violations_after == []


def test_css_skips_comment_lines(tmp_path: Path) -> None:
    """Pure CSS comment lines containing px are not flagged."""
    content = "/* width: 120px — old value */\n.foo { color: red; }\n"
    f = _write_css(tmp_path, "test.css", content)
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_css_non_layout_prop_ignored(tmp_path: Path) -> None:
    """Non-layout properties (font-size, border-radius, etc.) are not flagged."""
    content = ".foo { font-size: 13px; border-radius: 6px; letter-spacing: 0.5px; }\n"
    f = _write_css(tmp_path, "test.css", content)
    violations = clt._scan_css_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


# ── JS: heuristic layout variable check ─────────────────────────────────────

def test_js_detects_layout_var_assignment(tmp_path: Path) -> None:
    """A hard-coded numeric assignment to a layout-hinted const should be flagged."""
    f = _write_js(tmp_path, "panel.js", "const PANEL_W = 300;\n")
    violations = clt._scan_js_file(f, ignore_list=[], root=tmp_path)
    assert len(violations) == 1
    assert "PANEL_W" in violations[0]


def test_js_accepts_token_read(tmp_path: Path) -> None:
    """An assignment using getLayoutTokenPx should NOT be flagged."""
    f = _write_js(
        tmp_path, "panel.js",
        "export var PANEL_W = getLayoutTokenPx('--flyout-w');\n"
    )
    violations = clt._scan_js_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_js_ignores_non_layout_names(tmp_path: Path) -> None:
    """Numeric constants with non-layout suffixes are not flagged."""
    f = _write_js(tmp_path, "misc.js", "const MAX_RETRIES = 5;\nconst TIMEOUT = 3000;\n")
    violations = clt._scan_js_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_js_ignores_tiny_values(tmp_path: Path) -> None:
    """Values <= 2 are treated as tiny offsets and ignored."""
    f = _write_js(tmp_path, "panel.js", "const BTN_GAP = 2;\n")
    violations = clt._scan_js_file(f, ignore_list=[], root=tmp_path)
    assert violations == []


def test_js_respects_ignore_list(tmp_path: Path) -> None:
    """A JS violation entry that matches the ignore list is suppressed."""
    f = _write_js(tmp_path, "panel.js", "const PANEL_W = 300;\n")
    violations_before = clt._scan_js_file(f, ignore_list=[], root=tmp_path)
    assert violations_before
    pattern = "PANEL_W = 300"
    violations_after = clt._scan_js_file(f, ignore_list=[pattern], root=tmp_path)
    assert violations_after == []


# ── main() CLI ───────────────────────────────────────────────────────────────

def test_main_exits_0_without_check_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() without --check always exits 0 even when violations exist."""
    # Patch scan functions to return a fake violation
    monkeypatch.setattr(clt, "scan_css", lambda ignore: ["css/foo.css:1:.foo { width: 99px; }"])
    monkeypatch.setattr(clt, "scan_js", lambda ignore: [])
    rc = clt.main([])
    assert rc == 0


def test_main_exits_1_with_check_flag_on_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    """main(--check) exits 1 when violations are found."""
    monkeypatch.setattr(clt, "scan_css", lambda ignore: ["css/foo.css:1:.foo { width: 99px; }"])
    monkeypatch.setattr(clt, "scan_js", lambda ignore: [])
    rc = clt.main(["--check"])
    assert rc == 1


def test_main_exits_0_with_check_flag_on_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """main(--check) exits 0 when no violations are found."""
    monkeypatch.setattr(clt, "scan_css", lambda ignore: [])
    monkeypatch.setattr(clt, "scan_js", lambda ignore: [])
    rc = clt.main(["--check"])
    assert rc == 0
