"""Unit tests for scripts/check-manifests.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ── Load the script as a module ─────────────────────────────────────────────

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-manifests.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_manifests", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cm = _load_module()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_js(tmp_path: Path, name: str, exports: list[str]) -> Path:
    lines = [f"export function {e}() {{}}" for e in exports]
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_py(tmp_path: Path, name: str, exports: list[str]) -> Path:
    lines = [f"def {e}(): pass" for e in exports]
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_readme(tmp_path: Path, exports_section: str) -> Path:
    text = f"# Test\n\n## Public exports\n\n{exports_section}\n\n## Imports from\n\n- none\n"
    p = tmp_path / "_README.md"
    p.write_text(text, encoding="utf-8")
    return p


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_accepts_present_js_export(tmp_path):
    """A claimed JS export that exists in the source file produces no errors."""
    _make_js(tmp_path, "foo.js", ["myFunc"])
    _make_readme(tmp_path, "- `foo.js`: `myFunc` — does a thing\n")
    errors = cm.check_dir(tmp_path)
    assert errors == []


def test_detects_missing_js_export(tmp_path):
    """A claimed JS export that is absent from the source file is an error."""
    _make_js(tmp_path, "foo.js", ["realFunc"])
    _make_readme(tmp_path, "- `foo.js`: `missingFunc` — does a thing\n")
    errors = cm.check_dir(tmp_path)
    assert any("missingFunc" in e for e in errors)


def test_accepts_present_py_export(tmp_path):
    """A claimed Python function that exists produces no errors."""
    _make_py(tmp_path, "bar.py", ["parse_qty"])
    _make_readme(tmp_path, "- `bar.py`: `parse_qty` — parses quantity\n")
    errors = cm.check_dir(tmp_path)
    assert errors == []


def test_detects_missing_py_export(tmp_path):
    """A claimed Python function that is absent from the source is an error."""
    _make_py(tmp_path, "bar.py", ["real_fn"])
    _make_readme(tmp_path, "- `bar.py`: `phantom_fn` — not here\n")
    errors = cm.check_dir(tmp_path)
    assert any("phantom_fn" in e for e in errors)


def test_no_readme_is_error(tmp_path):
    """A directory without a _README.md reports a missing-file error."""
    errors = cm.check_dir(tmp_path)
    assert len(errors) == 1
    assert "_README.md" in errors[0]


def test_no_public_exports_section_passes(tmp_path):
    """A _README.md with no Public exports section is treated as passing."""
    readme = tmp_path / "_README.md"
    readme.write_text("# Empty\n\n## Owns\n\nSomething.\n", encoding="utf-8")
    errors = cm.check_dir(tmp_path)
    assert errors == []
