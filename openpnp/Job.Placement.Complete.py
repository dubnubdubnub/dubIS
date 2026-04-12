# OpenPnP Event Script: Job.Placement.Complete
# Reports part consumption to dubIS inventory server after each placement.
# Deployed to: C:\Users\isaac\.openpnp2\scripts\Events\Job.Placement.Complete.py
#
# Jython 2.7 — Python 2.7 stdlib only (no pip packages).

import json
import os
import urllib2
import traceback
from java.lang import System as JSystem

DUBIS_URL = os.environ.get("DUBIS_URL", "http://127.0.0.1:7890")
QUEUE_PATH = os.path.expanduser("~/.openpnp2/dubis_queue.json")
TIMEOUT = 3  # seconds

# OpenPnP routes print() to log file only. Use System.out for stdout visibility.
def _log(msg):
    JSystem.out.println("dubIS: " + msg)
    JSystem.out.flush()


def _load_queue():
    """Load queued events from disk."""
    try:
        with open(QUEUE_PATH, "r") as f:
            return json.load(f)
    except (IOError, ValueError):
        return []


def _save_queue(queue):
    """Save queued events to disk (or delete file if empty)."""
    if not queue:
        try:
            os.remove(QUEUE_PATH)
        except OSError:
            pass
        return
    with open(QUEUE_PATH, "w") as f:
        json.dump(queue, f)


def _post_consume(part_id, qty=1):
    """POST a consumption event to dubIS. Returns True on success."""
    url = DUBIS_URL + "/api/consume"
    data = json.dumps({"part_id": part_id, "qty": qty})
    req = urllib2.Request(url, data, {"Content-Type": "application/json"})
    try:
        resp = urllib2.urlopen(req, timeout=TIMEOUT)
        try:
            body = json.loads(resp.read())
        finally:
            resp.close()
        if body.get("ok"):
            _log("consumed %dx %s (new_qty=%s)" % (qty, part_id, body.get("new_qty")))
            return True
        else:
            _log("server error for %s: %s" % (part_id, body.get("error")))
            return False
    except Exception as e:
        _log("connection failed for %s: %s" % (part_id, e))
        return False


def _flush_queue():
    """Try to send all queued events. Returns list of still-failed events."""
    queue = _load_queue()
    if not queue:
        return []
    remaining = []
    for event in queue:
        if not _post_consume(event["part_id"], event.get("qty", 1)):
            remaining.append(event)
            break  # Stop flushing on first failure (server likely down)
    # Keep any un-attempted events too
    if remaining:
        idx = queue.index(remaining[0])
        remaining = queue[idx:]
    _save_queue(remaining)
    if queue and not remaining:
        _log("flushed %d queued event(s)" % len(queue))
    return remaining


# ── Main ──────────────────────────────────────────────────

try:
    # Flush any previously queued events first
    _flush_queue()

    # Get part ID from the placement that just completed
    part_id = placement.getPart().getId()

    # Send consumption event
    if not _post_consume(part_id, 1):
        # Queue for retry on next placement
        queue = _load_queue()
        queue.append({"part_id": part_id, "qty": 1})
        _save_queue(queue)
        _log("queued %s for retry (%d in queue)" % (part_id, len(queue)))

except Exception:
    _log("script error:")
    traceback.print_exc(file=JSystem.out)
