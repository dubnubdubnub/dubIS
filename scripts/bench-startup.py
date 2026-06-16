#!/usr/bin/env python3
"""Benchmark dubIS startup time.

Two layers of measurement:

1. **Headless backend** (no GUI, fully repeatable): interpreter baseline,
   import cost of the app's module graph, and the inventory build+query the
   frontend triggers via ``rebuild_inventory`` — measured cold (no cache.db)
   and warm (cache.db present).

2. **End-to-end GUI** (launches the real window): runs ``app.pyw`` with
   ``DUBIS_BENCH_OUT`` set so app.pyw / app-init.js emit phase marks, waits for
   the ``js_inventory_loaded`` mark (grid data ready + first render), then kills
   the process and reports the phase breakdown. A WebView2 window flashes on
   screen each run — that's unavoidable; close any running dubIS first so the
   poll/PnP ports are free.

Usage:
    python scripts/bench-startup.py                 # headless + GUI, 5 runs each
    python scripts/bench-startup.py --runs 10
    python scripts/bench-startup.py --headless-only
    python scripts/bench-startup.py --gui-only
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")  # console is cp1252 by default on Windows
except (AttributeError, OSError):
    pass

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DB = os.path.join(APP_DIR, "data", "cache.db")
PY = sys.executable

# Imports app.pyw pulls in (minus the GUI-only `webview`, measured separately).
APP_IMPORTS = "import webview, inventory_api, pnp_server, poll_api"

# GUI phase marks, in expected emission order, with human labels.
GUI_MARKS = [
    ("py_start", "Process start → bench import"),
    ("imports_done", "Python imports (webview, api, servers)"),
    ("api_constructed", "InventoryApi() constructed"),
    ("on_ready", "webview.start → window shown (WebView2 up)"),
    ("js_pywebview_ready", "Window shown → JS bridge ready"),
    ("js_prefs_loaded", "load_preferences"),
    ("js_inventory_loaded", "rebuild_inventory + first grid render"),
]


# ── helpers ────────────────────────────────────────────────────────────────

def _fmt(seconds: float) -> str:
    return f"{seconds * 1000:8.1f} ms"


def _summary(samples: list[float]) -> str:
    if not samples:
        return "no samples"
    med = statistics.median(samples)
    lo, hi = min(samples), max(samples)
    return f"median {_fmt(med)}   (min {_fmt(lo)}, max {_fmt(hi)}, n={len(samples)})"


def _time_subprocess(code: str, runs: int) -> list[float]:
    """Run `python -c <code>` `runs` times, return wall-clock seconds each."""
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        subprocess.run([PY, "-c", code], cwd=APP_DIR, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        samples.append(time.perf_counter() - t0)
    return samples


# ── headless backend benchmark ──────────────────────────────────────────────

def bench_headless(runs: int) -> None:
    print("\n=== Headless backend (no GUI) ===\n")

    baseline = _time_subprocess("pass", runs)
    print(f"  Interpreter baseline (python -c pass)   {_summary(baseline)}")

    full = _time_subprocess(APP_IMPORTS, runs)
    print(f"  Import app module graph (cold proc)     {_summary(full)}")
    med_base = statistics.median(baseline)
    med_full = statistics.median(full)
    print(f"    → import cost above baseline:         ~{_fmt(med_full - med_base)}")

    # In-process build+query, cold (no cache) and warm (cache present).
    for label, drop_cache in (("cold (no cache.db)", True), ("warm (cache.db present)", False)):
        samples = _bench_build(runs, drop_cache=drop_cache)
        print(f"  rebuild_inventory + query [{label:<24}] {_summary(samples)}")


def _bench_build(runs: int, *, drop_cache: bool) -> list[float]:
    """Measure InventoryApi() + rebuild_inventory() + query in a fresh subprocess.

    Each iteration is its own process so import/module state never carries over.
    The timed region excludes imports (measured separately above).
    """
    code = f"""
import os, time
{'os.path.exists({0!r}) and os.remove({0!r})'.format(CACHE_DB) if drop_cache else 'pass'}
import inventory_api, cache_db
t0 = time.perf_counter()
api = inventory_api.InventoryApi()
inv = api.rebuild_inventory()
rows = cache_db.query_inventory(api._get_cache())
api.shutdown()
print(time.perf_counter() - t0)
print(len(rows))
"""
    samples = []
    for _ in range(runs):
        out = subprocess.run([PY, "-c", code], cwd=APP_DIR, check=True,
                             capture_output=True, text=True)
        samples.append(float(out.stdout.splitlines()[0]))
    return samples


# ── end-to-end GUI benchmark ──────────────────────────────────────────────────

def bench_gui(runs: int, *, cold: bool) -> None:
    label = "cold cache" if cold else "warm cache"
    print(f"\n=== End-to-end GUI launch ({label}, {runs} runs) ===")
    print("    (a WebView2 window flashes each run; close any running dubIS first)\n")

    runs_marks: list[dict[str, float]] = []
    for i in range(runs):
        if cold and os.path.exists(CACHE_DB):
            os.remove(CACHE_DB)
        marks = _run_gui_once()
        if marks:
            runs_marks.append(marks)
            total = marks.get("js_inventory_loaded")
            print(f"  run {i + 1}: total to interactive = {_fmt(total) if total else 'FAILED'}")
        else:
            print(f"  run {i + 1}: FAILED (no marks captured)")

    if not runs_marks:
        print("\n  No successful GUI runs.")
        return

    print("\n  Phase breakdown (median across runs):")
    print(f"    {'phase':<46} {'cumulative':>12}   {'delta':>10}")
    prev_key = None
    for key, human in GUI_MARKS:
        vals = [m[key] for m in runs_marks if key in m]
        if not vals:
            continue
        cum = statistics.median(vals)
        if prev_key is None:
            delta = cum
        else:
            deltas = [m[key] - m[prev_key] for m in runs_marks if key in m and prev_key in m]
            delta = statistics.median(deltas) if deltas else 0.0
        print(f"    {human:<46} {_fmt(cum):>12}   {_fmt(delta):>10}")
        prev_key = key

    totals = [m["js_inventory_loaded"] for m in runs_marks if "js_inventory_loaded" in m]
    print(f"\n  Total launch → interactive grid: {_summary(totals)}")

    # Decompose the "window shown → JS bridge ready" gap using JS navigation
    # timing (relative to page navigation start, in ms).
    import json
    details = [m["js_pywebview_ready__detail"] for m in runs_marks
               if "js_pywebview_ready__detail" in m]
    if details:
        try:
            nav = json.loads(details[-1])
            print("\n  Within that gap, JS-side navigation timing (ms since page load start):")
            print(f"    response received (HTML)     {nav.get('responseEnd', '?'):>6} ms")
            print(f"    DOMContentLoaded             {nav.get('domContentLoaded', '?'):>6} ms")
            print(f"    DOM complete (modules done)  {nav.get('domComplete', '?'):>6} ms")
            print(f"    load event end               {nav.get('loadEnd', '?'):>6} ms")
            print(f"    bridge-ready (performance.now) {nav.get('now', '?'):>4} ms")
        except (ValueError, TypeError):
            pass


def _run_gui_once(timeout: float = 60.0) -> dict[str, float] | None:
    """Launch app.pyw with bench instrumentation, wait for the final mark, kill it."""
    fd, out_path = tempfile.mkstemp(suffix=".bench")
    os.close(fd)
    os.remove(out_path)  # bench.py appends; start clean
    env = dict(os.environ, DUBIS_BENCH_OUT=out_path)
    proc = subprocess.Popen([PY, os.path.join(APP_DIR, "app.pyw")], cwd=APP_DIR, env=env)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            marks = _read_marks(out_path)
            if "js_inventory_loaded" in marks:
                return marks
            if proc.poll() is not None:
                break  # process died early
            time.sleep(0.05)
        return _read_marks(out_path) or None
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.remove(out_path)
        except OSError:
            pass


def _read_marks(path: str) -> dict[str, float]:
    marks: dict[str, float] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    marks[parts[0]] = float(parts[1])
                if len(parts) >= 3 and parts[2]:
                    marks[f"{parts[0]}__detail"] = parts[2]  # stored alongside, ignored by float math
    except (OSError, ValueError):
        pass
    return marks  # type: ignore[return-value]


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark dubIS startup time.")
    ap.add_argument("--runs", type=int, default=5, help="iterations per measurement (default 5)")
    ap.add_argument("--headless-only", action="store_true")
    ap.add_argument("--gui-only", action="store_true")
    args = ap.parse_args()

    if not args.gui_only:
        bench_headless(args.runs)
    if not args.headless_only:
        bench_gui(args.runs, cold=False)
        bench_gui(max(2, args.runs // 2), cold=True)


if __name__ == "__main__":
    main()
