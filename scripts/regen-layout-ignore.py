"""Regenerate scripts/check-layout-tokens.ignore from the current CSS/JS state.

The ignore file is a line-anchored baseline of accepted hard-coded px layout
values. Because entries are anchored by line number, any intentional CSS edit
that inserts or removes lines shifts existing anchors and makes the baseline
stale (the layout-token guard then reports the shifted lines as "new"). Run
this after such an edit to re-anchor every entry to its current line and fold
in any newly-added hard-codes:

    python scripts/regen-layout-ignore.py

Then review the diff and commit. This grandfathers current px values exactly as
the original baseline did; migrating them to tokens remains a separate cleanup.
"""
from __future__ import annotations

import importlib.util
from collections import OrderedDict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IGNORE_FILE = REPO_ROOT / "scripts" / "check-layout-tokens.ignore"

_HEADER = """# check-layout-tokens.ignore
# Each non-blank, non-comment line is a substring pattern matched against
# "<file>:<lineno>:<text>" violation entries.
#
# Lines here are KNOWN ACCEPTED hard-codes (baseline). Future PRs should shrink
# this list by migrating values to tokens. Regenerate after intentional CSS
# edits with: python scripts/regen-layout-ignore.py
#
# Format: any substring of "<relpath>:<lineno>:<stripped-line>".
"""


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_layout_tokens", REPO_ROOT / "scripts" / "check-layout-tokens.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    clt = _load_checker()
    # Scan with an empty ignore list so every current hard-code is captured.
    violations = clt.scan_css([]) + clt.scan_js([])

    groups: OrderedDict[str, list[int]] = OrderedDict()
    for entry in violations:
        rel, lineno, _text = entry.split(":", 2)
        groups.setdefault(rel, []).append(int(lineno))

    out = [_HEADER]
    for rel, linenos in groups.items():
        bar = "─" * max(1, 60 - len(rel))
        out.append(f"\n# ── {rel} {bar}")
        out.extend(f"{rel}:{ln}:" for ln in linenos)

    IGNORE_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    total = sum(len(v) for v in groups.values())
    print(f"Wrote {total} entries across {len(groups)} files to {IGNORE_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
