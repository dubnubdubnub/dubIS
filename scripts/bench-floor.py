#!/usr/bin/env python3
"""Measure the irreducible floor of the pywebview/WebView2 stack.

Launches a *bare* window — trivial inline HTML, no inventory, no JS module
graph, no servers — and times process-start → window-shown → first JS bridge
round-trip. Subtracting this from the real app's startup tells us how much of
the ~1s is intrinsic framework cost vs. our own content/code.

Run via the orchestrator-style child process so timing matches bench-startup.py:
    python scripts/bench-floor.py            # parent: runs N iterations, aggregates
    python scripts/bench-floor.py --child    # one launch (used internally)
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
sys.path.insert(0, APP_DIR)  # so the --child process can `import bench`


def _child() -> None:
    """One bare-window launch. Emits bench marks then signals ready via the bridge."""
    import bench
    bench.mark("py_start")
    import webview
    bench.mark("imports_done")

    class Api:
        def ready(self) -> bool:
            import bench
            bench.mark("bridge_ready")
            return True

    html = (
        "<!doctype html><meta charset=utf-8><title>floor</title>"
        "<body><h1>floor</h1>"
        "<script>window.addEventListener('pywebviewready',function(){"
        "window.pywebview.api.ready();});</script>"
    )
    webview.create_window("floor", html=html, js_api=Api())

    def on_ready() -> None:
        bench.mark("on_ready")

    profile = os.path.join(APP_DIR, "data", "webview2")
    webview.start(func=on_ready, private_mode=False, storage_path=profile)


def _run_once(timeout: float = 60.0) -> dict[str, float] | None:
    fd, out = tempfile.mkstemp(suffix=".floor")
    os.close(fd)
    os.remove(out)
    env = dict(os.environ, DUBIS_BENCH_OUT=out)
    proc = subprocess.Popen([PY, os.path.abspath(__file__), "--child"], cwd=APP_DIR, env=env)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            marks = _read(out)
            if "bridge_ready" in marks:
                return marks
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        return _read(out) or None
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.remove(out)
        except OSError:
            pass


def _read(path: str) -> dict[str, float]:
    marks: dict[str, float] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    marks[parts[0]] = float(parts[1])
    except (OSError, ValueError):
        pass
    return marks


def _fmt(s: float) -> str:
    return f"{s * 1000:7.1f} ms"


def main() -> None:
    if "--child" in sys.argv:
        _child()
        return
    runs = 8
    print(f"\n=== Bare pywebview/WebView2 floor ({runs} runs) ===")
    print("    (blank window, no app content; window flashes each run)\n")
    rows = [m for m in (_run_once() for _ in range(runs)) if m and "bridge_ready" in m]
    if not rows:
        print("  No successful runs.")
        return
    for key, human in [
        ("imports_done", "import webview (CLR/pythonnet)"),
        ("on_ready", "webview.start → window shown"),
        ("bridge_ready", "window shown → blank page + bridge ready"),
    ]:
        cum = statistics.median(m[key] for m in rows if key in m)
        print(f"  {human:<44} cum {_fmt(cum)}")
    totals = [m["bridge_ready"] for m in rows]
    print(f"\n  Floor: launch → blank window interactive: "
          f"median {_fmt(statistics.median(totals))} "
          f"(min {_fmt(min(totals))}, max {_fmt(max(totals))}, n={len(totals)})")


if __name__ == "__main__":
    main()
