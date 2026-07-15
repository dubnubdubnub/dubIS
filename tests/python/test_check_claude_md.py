"""check-claude-md.py — CLAUDE.md path references must exist."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-claude-md.py"


def _run(md_text, tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text(md_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(md), "--root", str(REPO)],
        capture_output=True, text=True,
    )


def test_existing_path_passes(tmp_path):
    assert _run("see `inventory_api.py` and `domain/inventory.py`", tmp_path).returncode == 0


def test_missing_path_fails(tmp_path):
    r = _run("see `css/styles.css`", tmp_path)
    assert r.returncode == 1
    assert "css/styles.css" in r.stdout


def test_runtime_paths_skipped(tmp_path):
    assert _run("see `data/digikey_cookies.json` and `events/part_events.csv`",
                tmp_path).returncode == 0


def test_non_path_tokens_skipped(tmp_path):
    assert _run("run `bash scripts/verify.sh --e2e` or set `DUBIS_WEBVIEW_PROFILE`",
                tmp_path).returncode == 0


def test_real_claude_md_passes():
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
