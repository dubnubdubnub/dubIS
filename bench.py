"""Startup timing instrumentation.

A no-op unless the env var ``DUBIS_BENCH_OUT`` points at a file. When set,
``mark(label)`` appends ``<label>\\t<seconds-since-process-start>`` lines that
``scripts/bench-startup.py`` reads back to produce a phase breakdown.

The first ``mark()`` (or import of this module) fixes the t0 reference using
``time.perf_counter()``. JS-side marks come in through ``InventoryApi.bench_mark``
which simply calls ``mark()`` here, so every timestamp is taken on one Python
clock — no JS/Python clock-skew to reconcile.
"""

from __future__ import annotations

import os
import time

# Fixed as early as this module is first imported. app.pyw imports it before the
# heavy imports (webview, CLR) so t0 is close to true process start.
_T0 = time.perf_counter()

_OUT_PATH = os.environ.get("DUBIS_BENCH_OUT")
ENABLED = bool(_OUT_PATH)


def mark(label: str, detail: str = "") -> None:
    """Record a phase timestamp. No-op unless DUBIS_BENCH_OUT is set.

    ``detail`` is an optional free-form string (e.g. JSON of JS navigation
    timing) written as a third tab-separated column.
    """
    if not ENABLED:
        return
    elapsed = time.perf_counter() - _T0
    try:
        with open(_OUT_PATH, "a", encoding="utf-8") as f:
            f.write(f"{label}\t{elapsed:.6f}\t{detail}\n")
    except OSError:
        # Never let instrumentation break startup.
        pass
