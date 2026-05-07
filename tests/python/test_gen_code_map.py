"""Tests for scripts/gen-code-map.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the script importable as a module
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import importlib
gen_code_map = importlib.import_module("gen-code-map")


# ── Python import parsing ─────────────────────────────────────────────

def test_parse_python_imports_finds_internal(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("import bar\nfrom baz import qux\n")
    (tmp_path / "bar.py").write_text("")
    (tmp_path / "baz.py").write_text("")
    internal = {"bar.py", "baz.py"}

    imports = gen_code_map.parse_python_imports(tmp_path / "foo.py", internal)
    assert imports == ["bar.py", "baz.py"]


def test_parse_python_imports_skips_external(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text(
        "import os\n"
        "import json\n"
        "import requests\n"
        "from pathlib import Path\n"
        "import bar\n"
    )
    (tmp_path / "bar.py").write_text("")
    internal = {"bar.py"}

    imports = gen_code_map.parse_python_imports(tmp_path / "foo.py", internal)
    assert imports == ["bar.py"]


def test_parse_python_imports_handles_dotted(tmp_path: Path) -> None:
    pkg = tmp_path / "domain"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "inventory.py").write_text("")
    (tmp_path / "user.py").write_text("from domain.inventory import foo\nimport domain.inventory\n")
    internal = {"domain/inventory.py"}

    imports = gen_code_map.parse_python_imports(tmp_path / "user.py", internal)
    assert imports == ["domain/inventory.py"]


def test_parse_python_imports_returns_sorted_unique(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text(
        "import bar\nimport baz\nimport bar\nfrom baz import x\n"
    )
    (tmp_path / "bar.py").write_text("")
    (tmp_path / "baz.py").write_text("")
    internal = {"bar.py", "baz.py"}

    imports = gen_code_map.parse_python_imports(tmp_path / "foo.py", internal)
    assert imports == ["bar.py", "baz.py"]
