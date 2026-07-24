"""Exhaustiveness guard: the SSE event vocabulary is symmetric — every event
type the backend publishes has a frontend handler, and every frontend handler
listens for a type the backend actually publishes.

Renaming an SSE event on one side only is a SILENT failure: no exception, no
other test breaks, the feature just quietly stops updating. This guard makes
that a red build. It pairs with test_mutation_publishes.py (which proves
mutations publish) to lock the whole SSE freshness pipeline end to end.

Static string-literal scan (both sides use bare literals, no interpolation):
  backend  publish("<type>")   in server/*.py + pnp_server.py
  frontend onEvent("<type>")   in js/**/*.js
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]

_PUBLISH_RE = re.compile(r"""\bpublish\(\s*["']([a-z][\w.]+)["']""")
_ONEVENT_RE = re.compile(r"""\bonEvent\(\s*["']([a-z][\w.]+)["']""")

# Types that legitimately exist on only one side (justify each).
PUBLISH_ONLY_ALLOWED: set[str] = set()
HANDLER_ONLY_ALLOWED: set[str] = set()   # handled, published elsewhere (e.g. non-py source)


def _scan(paths, pattern) -> set[str]:
    found = set()
    for p in paths:
        found |= set(pattern.findall(p.read_text(encoding="utf-8", errors="ignore")))
    return found


def _backend_files():
    files = list((_ROOT / "server").rglob("*.py"))
    files.append(_ROOT / "pnp_server.py")
    return [f for f in files if "test" not in f.name and "spike" not in f.name]


def _frontend_files():
    return [f for f in (_ROOT / "js").rglob("*.js")]


def test_sse_event_vocabulary_is_symmetric():
    published = _scan(_backend_files(), _PUBLISH_RE)
    handled = _scan(_frontend_files(), _ONEVENT_RE)
    assert published, "found no backend publish(...) literals — scan is broken"
    assert handled, "found no frontend onEvent(...) literals — scan is broken"

    published_without_handler = sorted(published - handled - PUBLISH_ONLY_ALLOWED)
    handled_without_publisher = sorted(handled - published - HANDLER_ONLY_ALLOWED)

    assert not published_without_handler, (
        "Backend publishes SSE event(s) no frontend onEvent() handles — the push "
        "is silently dropped. Add a handler or add to PUBLISH_ONLY_ALLOWED:\n  "
        + "\n  ".join(published_without_handler)
    )
    assert not handled_without_publisher, (
        "Frontend onEvent() handler(s) listen for SSE event(s) the backend never "
        "publishes — a dead handler (likely a typo/rename). Fix the name or add to "
        "HANDLER_ONLY_ALLOWED:\n  " + "\n  ".join(handled_without_publisher)
    )
