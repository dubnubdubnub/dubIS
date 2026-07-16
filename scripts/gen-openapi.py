#!/usr/bin/env python3
"""Generate docs/openapi-v1.json from the /v1 FastAPI app's OpenAPI schema.

Usage:
    python scripts/gen-openapi.py          # write the snapshot
    python scripts/gen-openapi.py --check  # exit 1 if the snapshot is stale

The app is built with a stub api object (`types.SimpleNamespace()`) — route
registration in `server.app.create_app` never touches the api (only request
handlers do), so no real `InventoryApi`/data directory is needed to generate
the schema.

Mirrors scripts/gen-inventory-types.py's --check pattern exactly.
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "openapi-v1.json"

# Make sure the repo root is on sys.path so `server` is importable.
sys.path.insert(0, str(REPO_ROOT))

from server.app import create_app  # noqa: E402


def render_openapi() -> str:
    """Render the /v1 app's OpenAPI schema as pretty-printed, sorted JSON."""
    app = create_app(types.SimpleNamespace())
    spec = app.openapi()
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help="Output path for the OpenAPI snapshot (default: docs/openapi-v1.json)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if the output file is missing or stale (does not write)",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    rendered = render_openapi()

    if args.check:
        if not out.exists():
            print(
                f"error: {out} does not exist. "
                "Run `python scripts/gen-openapi.py` and commit.",
                file=sys.stderr,
            )
            return 1
        existing = out.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"error: {out} is stale. "
                "Run `python scripts/gen-openapi.py` and commit.",
                file=sys.stderr,
            )
            return 1
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
