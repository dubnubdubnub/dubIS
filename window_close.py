"""Window-close decision logic, extracted from app.pyw's on_closing so it can
be unit-tested without importing pywebview.

Why this exists as its own module: the close-confirmation flow deadlocked the
app (window "Not Responding", refuses to close) whenever the BOM was dirty.
on_closing runs on the WinForms UI thread — pywebview constructs the `closing`
event with should_lock=True (webview/window.py), so its handlers execute
synchronously on the thread dispatching FormClosing. window.evaluate_js is a
*blocking* call: it posts the script to WebView2 and waits on a Semaphore that
is released only by a completion callback marshaled back onto that same UI
thread's message pump. Calling it synchronously from on_closing means the pump
is blocked in the wait and can never run the callback -> permanent deadlock.
(Confirmed live with py-spy: the UI thread parked in Semaphore.acquire inside
evaluate_js under on_closing.)

The fix: open the JS modal on a worker thread and veto the close immediately.
Returning False lets the message pump resume, so the marshaled evaluate_js call
gets serviced and the modal actually appears. The user's choice in the modal
then re-triggers the close through confirm_close() (which sets _force_close and
destroys the window), landing in the fast-exit branch below with no evaluate_js.
"""

import logging
import threading

logger = logging.getLogger(__name__)


def handle_closing(*, force_close, bom_dirty, open_modal, do_exit):
    """Decide what to do when the OS asks the window to close.

    Args:
        force_close: api._force_close — the user already confirmed via the modal.
        bom_dirty: api._bom_dirty — there are unsaved BOM changes.
        open_modal: zero-arg callable that shows the JS confirm modal. MUST be
            treated as blocking (it wraps window.evaluate_js); this function
            never calls it on the caller's thread.
        do_exit: zero-arg callable that tears down and terminates the process.

    Returns:
        True to allow the close (caller need not cancel; in practice do_exit()
        has already terminated the process). False to veto the close while the
        confirm modal is shown asynchronously.
    """
    if force_close or not bom_dirty:
        do_exit()
        return True

    # Unsaved changes: show the confirm modal WITHOUT blocking the UI thread.
    def _worker():
        try:
            open_modal()
        except Exception as exc:
            # Bridge unavailable / modal failed to open: don't wedge the window
            # open — fall back to exiting.
            logger.warning("Could not show close modal: %s", exc)
            do_exit()

    threading.Thread(target=_worker, name="dubis-close-modal", daemon=True).start()
    return False
