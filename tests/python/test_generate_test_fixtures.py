"""The fixture generator must never overwrite committed fixtures with nothing.

`tests/vitest-global-setup.js` regenerates fixtures as a local convenience when
they look stale. Run under an interpreter without the project's dependencies
(macOS ships a /usr/bin/python3 with no xlrd), every .xls conversion raised, the
blanket `except Exception: continue` read that as "there are no .xls files", and
the committed `xls-conversions.json` was rewritten as `{}` — so JS tests then
failed on fixture values for a reason unrelated to anything the developer
touched.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_generator():
    """Import the hyphenated script by path — it is not an importable module."""
    path = REPO_ROOT / "scripts" / "generate-test-fixtures.py"
    spec = importlib.util.spec_from_file_location("_generate_test_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_generate_test_fixtures"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generator():
    return _load_generator()


def test_unconvertible_xls_files_raise_rather_than_yielding_an_empty_dict(generator, monkeypatch):
    """The destructive case: files present, every conversion failing."""
    def boom(_path):
        raise ModuleNotFoundError("No module named 'xlrd'")

    monkeypatch.setattr(generator.csv_io, "convert_xls_to_csv", boom)
    with pytest.raises(RuntimeError) as excinfo:
        generator.generate_xls_conversions()
    message = str(excinfo.value)
    assert "refusing to write an empty fixture" in message
    # The message has to name the real cause, or the next person debugs the
    # fixture instead of their interpreter.
    assert "xlrd" in message
    assert ".venv" in message


def test_a_healthy_run_still_converts_the_committed_xls_files(generator):
    """The repo does ship .xls fixtures, so this is not a vacuous pass."""
    conversions = generator.generate_xls_conversions()
    assert conversions, "expected the committed .xls fixtures to convert"
    for payload in conversions.values():
        assert payload["csv_text"]
        assert payload["headers"]


def test_one_bad_file_among_good_ones_warns_without_failing(generator, monkeypatch, capsys):
    """A single unreadable file must not block the whole regeneration — only a
    total failure is evidence of a broken environment."""
    real = generator.csv_io.convert_xls_to_csv
    seen = {"n": 0}

    def flaky(path):
        seen["n"] += 1
        if seen["n"] == 1:
            raise ValueError("corrupt workbook")
        return real(path)

    monkeypatch.setattr(generator.csv_io, "convert_xls_to_csv", flaky)
    conversions = generator.generate_xls_conversions()
    assert conversions
    assert "WARNING" in capsys.readouterr().out


def test_no_xls_files_at_all_is_an_empty_dict_not_an_error(generator, monkeypatch, tmp_path):
    """An empty result is only wrong when files existed and could not be read."""
    empty = str(tmp_path)
    monkeypatch.setattr(generator, "DATA_DIR", empty)
    monkeypatch.setattr(generator, "E2E_FIXTURES", empty)
    monkeypatch.setattr(generator, "FIXTURES_DIR", empty)
    assert generator.generate_xls_conversions() == {}
