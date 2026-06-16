#!/usr/bin/env python3
"""Benchmark dubIS shutdown (close) time.

Launches app.pyw with DUBIS_BENCH_CLOSE=1, which makes the app trigger its own
close once the grid is interactive (window.destroy() → FormClosing → on_closing,
the same path the X button takes). The harness times wall-clock from the close
trigger to the process actually exiting, and reads the bench marks to break the
teardown into phases:

    close_trigger   destroy() called (grid was interactive)
    closing_enter   our on_closing handler entered  (gap = WinForms/WebView2 dispatch)
    pnp_stopped     stop_pnp_server() returned       (gap = server.shutdown + close)
    cache_closed    api.shutdown() returned          (gap = SQLite commit/close)
    pre_exit        immediately before os._exit(0)
    <process death> measured externally              (gap = os._exit + OS teardown)

Usage:
    python scripts/bench-close.py            # 6 runs, aggregate
    python scripts/bench-close.py --runs 10
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
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

# Phase marks in order, with the gap each represents.
PHASES = [
    ("close_trigger", "(start: destroy() called)"),
    ("closing_enter", "dispatch → on_closing (WinForms/WebView2)"),
    ("pnp_stopped", "stop_pnp_server (server.shutdown + close)"),
    ("cache_closed", "api.shutdown (SQLite commit/close)"),
    ("pre_exit", "→ os._exit"),
]


def _fmt(s: float) -> str:
    return f"{s * 1000:8.1f} ms"


def _summary(samples: list[float]) -> str:
    if not samples:
        return "no samples"
    return (f"median {_fmt(statistics.median(samples))}   "
            f"(min {_fmt(min(samples))}, max {_fmt(max(samples))}, n={len(samples)})")


def _read_marks(path: str) -> dict[str, float]:
    marks: dict[str, float] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    try:
                        marks[parts[0]] = float(parts[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return marks


def _run_once(timeout: float = 60.0) -> tuple[dict[str, float], float] | None:
    """Launch, let it self-close, return (marks, externally-measured death time)."""
    fd, out = tempfile.mkstemp(suffix=".close")
    os.close(fd)
    os.remove(out)
    env = dict(os.environ, DUBIS_BENCH_OUT=out, DUBIS_BENCH_CLOSE="1")
    proc = subprocess.Popen([PY, os.path.join(APP_DIR, "app.pyw")], cwd=APP_DIR, env=env)

    # Wait for the close to be triggered so we can timestamp process death on the
    # same clock as the marks (perf_counter epoch differs per process, so we align
    # on wall-clock deltas: death_wall - trigger_wall, then express vs close_trigger).
    deadline = time.monotonic() + timeout
    trigger_wall = None
    while time.monotonic() < deadline:
        marks = _read_marks(out)
        if trigger_wall is None and "close_trigger" in marks:
            trigger_wall = time.monotonic()
        if proc.poll() is not None:
            death_wall = time.monotonic()
            break
        time.sleep(0.02)
    else:
        proc.kill()
        try:
            os.remove(out)
        except OSError:
            pass
        return None

    marks = _read_marks(out)
    try:
        os.remove(out)
    except OSError:
        pass
    if "close_trigger" not in marks or trigger_wall is None:
        return None
    # External death measured relative to the trigger, in the harness clock.
    death_since_trigger = death_wall - trigger_wall
    return marks, death_since_trigger


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark dubIS close/shutdown time.")
    ap.add_argument("--runs", type=int, default=6)
    args = ap.parse_args()

    print(f"\n=== dubIS close/shutdown ({args.runs} runs) ===")
    print("    (launches the app, lets it self-close once interactive; window flashes)\n")

    rows: list[tuple[dict[str, float], float]] = []
    for i in range(args.runs):
        res = _run_once()
        if not res:
            print(f"  run {i + 1}: FAILED")
            continue
        marks, death = res
        total = death  # trigger → process death
        rows.append(res)
        print(f"  run {i + 1}: close → exit = {_fmt(total)}")

    if not rows:
        print("\n  No successful runs.")
        return

    print("\n  Phase breakdown (median across runs, relative to close_trigger):")
    print(f"    {'phase gap':<46} {'delta':>11}")
    prev = "close_trigger"
    for key, label in PHASES[1:]:
        deltas = [m[key] - m[prev] for m, _ in rows if key in m and prev in m]
        if deltas:
            print(f"    {label:<46} {_fmt(statistics.median(deltas)):>11}")
        prev = key
    # pre_exit → actual process death (os._exit + OS teardown), measured externally.
    tail = []
    for m, death in rows:
        if "pre_exit" in m and "close_trigger" in m:
            tail.append(death - (m["pre_exit"] - m["close_trigger"]))
    if tail:
        print(f"    {'os._exit → process death (OS teardown)':<46} {_fmt(statistics.median(tail)):>11}")

    totals = [death for _, death in rows]
    print(f"\n  Total close → process exit: {_summary(totals)}")


if __name__ == "__main__":
    main()
