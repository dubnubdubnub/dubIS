"""Tests for scripts/gen-code-map.py."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Make the script importable as a module
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

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


# ── JS import parsing ─────────────────────────────────────────────────

def test_parse_js_imports_named(tmp_path: Path) -> None:
    src = tmp_path / "panel.js"
    src.write_text(
        "import { foo, bar } from './logic.js';\n"
        "import { baz } from '../utils.js';\n"
    )
    (tmp_path / "logic.js").write_text("")
    (tmp_path / "utils.js").write_text("")  # Note: import is "../utils.js" so resolves above tmp_path; not internal in this fixture.
    internal = {"panel.js", "logic.js"}

    imports = gen_code_map.parse_js_imports(src, tmp_path, internal)
    assert imports == ["logic.js"]


def test_parse_js_imports_default_and_namespace(tmp_path: Path) -> None:
    src = tmp_path / "panel.js"
    src.write_text(
        "import store from './store.js';\n"
        "import * as helpers from './helpers.js';\n"
    )
    (tmp_path / "store.js").write_text("")
    (tmp_path / "helpers.js").write_text("")
    internal = {"panel.js", "store.js", "helpers.js"}

    imports = gen_code_map.parse_js_imports(src, tmp_path, internal)
    assert imports == ["helpers.js", "store.js"]


def test_parse_js_imports_skips_external(tmp_path: Path) -> None:
    src = tmp_path / "panel.js"
    src.write_text(
        "import { x } from 'lodash';\n"
        "import y from '@anthropic-ai/sdk';\n"
        "import { z } from './local.js';\n"
    )
    (tmp_path / "local.js").write_text("")
    internal = {"panel.js", "local.js"}

    imports = gen_code_map.parse_js_imports(src, tmp_path, internal)
    assert imports == ["local.js"]


def test_parse_js_imports_resolves_relative(tmp_path: Path) -> None:
    sub = tmp_path / "feature"
    sub.mkdir()
    src = sub / "panel.js"
    src.write_text(
        "import { a } from '../store.js';\n"
        "import { b } from './logic.js';\n"
    )
    (tmp_path / "store.js").write_text("")
    (sub / "logic.js").write_text("")
    internal = {"store.js", "feature/panel.js", "feature/logic.js"}

    imports = gen_code_map.parse_js_imports(src, tmp_path, internal)
    assert imports == ["feature/logic.js", "store.js"]


def test_parse_js_imports_handles_multiline(tmp_path: Path) -> None:
    src = tmp_path / "panel.js"
    src.write_text(
        "import {\n  a,\n  b,\n  c,\n} from './big.js';\n"
    )
    (tmp_path / "big.js").write_text("")
    internal = {"panel.js", "big.js"}

    imports = gen_code_map.parse_js_imports(src, tmp_path, internal)
    assert imports == ["big.js"]
