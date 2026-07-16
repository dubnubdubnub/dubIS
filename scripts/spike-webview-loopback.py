"""Manual spike (Phase 1b, Task 1): verify `fetch()` + `EventSource` work
against the /v1 loopback server from *inside* a WebView2 window with NO
`js_api` bridge attached.

This is the load-bearing assumption for the whole phase: the desktop app is
going to become a browser pointed at `http://127.0.0.1:<port>/`, with all
JS<->Python traffic going over HTTP/SSE instead of the pywebview bridge. If
WebView2 can't do plain `fetch` + `EventSource` against a loopback origin,
that plan is dead before it starts.

Run manually on Windows (a window will briefly appear and close itself):

    python scripts/spike-webview-loopback.py

Prints one of:
    SPIKE PASS: fetch+SSE OK in WebView2      (exit 0)
    SPIKE FAIL: <observed document.title>     (exit 1)

If it fails specifically on the SSE step (document.title stuck at
"FETCH_OK" or shows "SSE_ERR"), STOP and report BLOCKED — the SSE transport
decision in the Phase 1b design doc needs revisiting, not a silent
workaround.

Observed finding (2026-07-16, this machine): WebView2's EventSource does
NOT reliably dispatch the `open` DOM event — readyState transitions to
OPEN (1) but `onopen` sometimes never fires (~50% of runs in manual
testing). Real *message* delivery (a named `event:` frame pushed through
the broker) worked 100% of runs once the browser-side readyState reached
OPEN. Conclusion: SSE works in WebView2, but readiness must be detected by
readyState/message delivery, not by relying on the `open` event alone —
worth carrying into the real `sse.js` module in a later task.
"""

from __future__ import annotations

import socket
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import webview  # noqa: E402

from server.__main__ import _build_api  # noqa: E402
from server.run import start_server, stop_server  # noqa: E402

# Minimal page, no app JS at all: fetches /v1/health, then opens an SSE
# stream to /v1/events. Progress is painted into document.title (rather than
# the DOM) so the driving script can read it back via a single
# `evaluate_js("document.title")` call without needing to know DOM shape.
#
# SSE success is signalled by the EventSource's `open` event, not by
# `onmessage` firing: `/v1/events` emits `": heartbeat\n\n"` comment frames
# every ~15s to keep the connection alive, and SSE comments are consumed by
# the browser transport, never dispatched as `message` events. `onopen`
# firing means WebView2 completed the chunked-transfer streaming handshake,
# which is exactly the capability under test here. `onmessage` is still
# wired up in case a real event arrives first, as an equally-valid pass.
SPIKE_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>SPIKE_INIT</title></head>
<body>
<script>
document.title = 'SPIKE_INIT';

fetch('/v1/health')
  .then(r => r.json())
  .then(j => { document.title = j.ok ? 'FETCH_OK' : 'FETCH_BAD'; })
  .catch(e => { document.title = 'FETCH_ERR:' + e; });

try {
  const es = new EventSource('/v1/events');
  window.__es = es;
  es.onopen = () => { document.title = 'SSE_OK'; };
  es.onmessage = () => { document.title = 'SSE_OK'; };
  // Server always sends a named `event:` field (see server/events.py
  // format_frame) — named-event frames dispatch as that event type, not
  // the generic 'message' event, so a real /v1/events consumer (and this
  // spike) must addEventListener per event name rather than rely on
  // onmessage alone.
  es.addEventListener('spike.test', () => { document.title = 'SSE_OK'; });
  es.onerror = (e) => { if (document.title !== 'SSE_OK') document.title = 'SSE_ERR:' + es.readyState; };
  setInterval(() => {
    if (document.title.indexOf('SSE_OK') === -1 && document.title.indexOf('SSE_ERR') === -1) {
      document.title = 'FETCH_OK|rs=' + es.readyState;
    }
  }, 500);
} catch (e) {
  document.title = 'SSE_CTOR_ERR:' + e;
}
</script>
</body>
</html>
"""


def _free_port() -> int:
    """Bind an ephemeral port and release it immediately for uvicorn to reuse.

    Small TOCTOU risk (another process could grab it between release and
    uvicorn's bind) — acceptable for a manual, single-run spike script.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    data_dir = tempfile.mkdtemp(prefix="dubis-spike-data-")
    static_dir = tempfile.mkdtemp(prefix="dubis-spike-static-")
    (Path(static_dir) / "spike.html").write_text(SPIKE_HTML, encoding="utf-8")

    api = _build_api(data_dir)
    port = _free_port()
    server = start_server(api, port=port, static_dir=static_dir)

    start_deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < start_deadline:
        time.sleep(0.05)
    if not server.started:
        print("SPIKE FAIL: server did not start")
        return 1

    url = f"http://127.0.0.1:{port}/spike.html"
    window = webview.create_window("dubIS spike (no js_api)", url, width=400, height=300)

    result: dict[str, str] = {"state": "SPIKE_INIT"}

    def poll() -> None:
        from server import events as sse_events  # noqa: PLC0415

        poll_deadline = time.monotonic() + 30
        state = "SPIKE_INIT"
        published = False
        while time.monotonic() < poll_deadline:
            time.sleep(0.25)
            try:
                state = window.evaluate_js("document.title")
            except Exception as exc:  # window may already be gone
                state = f"EVAL_ERR:{exc}"
                break
            print(f"[poll] title={state!r}", flush=True)
            if state == "SSE_OK":
                break
            # Once the browser-side readyState reports OPEN, push a real
            # event through the broker so success is proven by actual
            # message delivery, not just the (separately flaky-observed)
            # 'open' DOM event dispatch.
            if not published and "rs=1" in state:
                sse_events.publish("spike.test", {"ok": True})
                published = True
        result["state"] = state
        window.destroy()

    webview.start(poll, debug=False)

    stop_server(server)
    api.shutdown()

    state = result["state"]
    if state == "SSE_OK":
        print("SPIKE PASS: fetch+SSE OK in WebView2")
        return 0
    print(f"SPIKE FAIL: {state}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
