"""Regression tests for the window-close decision (window_close.handle_closing).

The bug these guard against: on_closing runs on the WinForms UI thread
(pywebview's `closing` event is should_lock=True), and window.evaluate_js is a
*blocking* call whose completion callback is marshaled back to that same UI
thread's message pump. Calling evaluate_js synchronously from on_closing means
the pump can never run the callback -> permanent deadlock ("Not Responding",
window refuses to close). Reproduced live via py-spy on a hung process.

The fix: the unsaved-BOM path must open the JS modal OFF the calling thread and
veto the close immediately, so the message pump resumes and can service the
marshaled evaluate_js call.
"""

import threading
import time

from window_close import handle_closing


def test_clean_close_exits_immediately():
    """No unsaved changes -> exit right away, never touch the modal."""
    calls = []
    result = handle_closing(
        force_close=False,
        bom_dirty=False,
        open_modal=lambda: calls.append("modal"),
        do_exit=lambda: calls.append("exit"),
    )
    assert calls == ["exit"]
    assert result is True  # close allowed


def test_force_close_exits_even_when_dirty():
    """confirm_close() set _force_close -> exit even with a dirty BOM."""
    calls = []
    result = handle_closing(
        force_close=True,
        bom_dirty=True,
        open_modal=lambda: calls.append("modal"),
        do_exit=lambda: calls.append("exit"),
    )
    assert calls == ["exit"]
    assert result is True


def test_dirty_close_does_not_block_on_modal():
    """THE regression: a blocking open_modal (like the real evaluate_js
    deadlock) must NOT run on the calling thread. handle_closing must return
    promptly, vetoing the close, with the modal dispatched to a worker."""
    started = threading.Event()
    release = threading.Event()

    def blocking_modal():
        started.set()
        # Stand-in for evaluate_js blocking on a semaphore the UI thread can't
        # release. If handle_closing called this synchronously, the assertions
        # below would never run (deadlock) / elapsed would blow past the bound.
        release.wait(5)

    t0 = time.monotonic()
    result = handle_closing(
        force_close=False,
        bom_dirty=True,
        open_modal=blocking_modal,
        do_exit=lambda: None,
    )
    elapsed = time.monotonic() - t0

    assert result is False, "dirty close must be vetoed while the modal shows"
    assert elapsed < 0.5, f"handle_closing blocked on the modal ({elapsed:.2f}s)"
    assert started.wait(2), "modal was never opened (should run on a worker)"
    release.set()


def test_modal_failure_falls_back_to_exit():
    """If opening the modal raises (bridge gone), fall back to exiting so the
    window can't get wedged open."""
    calls = []
    done = threading.Event()

    def failing_modal():
        raise RuntimeError("bridge down")

    def do_exit():
        calls.append("exit")
        done.set()

    result = handle_closing(
        force_close=False,
        bom_dirty=True,
        open_modal=failing_modal,
        do_exit=do_exit,
    )
    assert result is False
    assert done.wait(2), "modal failure did not trigger exit"
    assert calls == ["exit"]
