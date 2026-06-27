"""dubIS-side mirror push client. Fire-and-forget; never raises into the app."""

import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone

from mirror_serialize import INVENTORY_CSV_FIELDS

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_payload(inventory, *, dubis_running, token, now_iso=None):
    return {
        "inventory": inventory,
        "csv_fields": list(INVENTORY_CSV_FIELDS),
        "pushed_at": now_iso or _now_iso(),
        "source": "dubis",
        "dubis_running": dubis_running,
        "token": token,
    }


def push_snapshot(payload, *, host="127.0.0.1", port=7892, timeout=2.0):
    """POST payload to the mirror daemon. Returns True on success; logs+returns
    False on any failure. Never raises."""
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/push", data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001 — non-fatal mirror push
        logger.warning("Mirror push failed: %s", exc)
        return False


class MirrorController:
    """Owns mirror push policy: enabled-gate, token lookup, async coalesced push."""

    def __init__(self, *, is_enabled, read_token, host="127.0.0.1", port=7892):
        self._is_enabled = is_enabled
        self._read_token = read_token
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._in_flight = False
        self._pending = None  # latest inventory awaiting a free slot

    def push_event(self, inventory, *, dubis_running, block=False):
        if not self._is_enabled():
            return
        token = self._read_token()
        if not token:
            logger.warning("Mirror enabled but no token available; skipping push")
            return
        payload = build_payload(inventory, dubis_running=dubis_running, token=token)

        def _do():
            push_snapshot(payload, host=self._host, port=self._port)

        if block:
            _do()
        else:
            threading.Thread(target=_do, name="mirror-push", daemon=True).start()

    def on_inventory_changed(self, inventory):
        """Coalesced async push for live changes (dubis_running=True)."""
        if not self._is_enabled():
            return
        with self._lock:
            self._pending = inventory
            if self._in_flight:
                return
            self._in_flight = True
        threading.Thread(target=self._drain, name="mirror-push", daemon=True).start()

    def _drain(self):
        while True:
            with self._lock:
                inv = self._pending
                self._pending = None
                if inv is None:
                    self._in_flight = False
                    return
            token = self._read_token()
            if token:
                push_snapshot(build_payload(inv, dubis_running=True, token=token),
                              host=self._host, port=self._port)
            else:
                logger.warning("Mirror push skipped: no token")
