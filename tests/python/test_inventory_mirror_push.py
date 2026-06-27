import json
import threading
import urllib.error
import urllib.request

import pytest

from inventory_mirror import SnapshotStore, make_push_server


@pytest.fixture
def push_server(tmp_path):
    store = SnapshotStore(str(tmp_path / "snap.json"))
    server = make_push_server(store, token="secret", port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address
    yield store, f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=3)


def test_valid_push_stores_snapshot(push_server):
    store, base = push_server
    resp = _post(base + "/push", {"inventory": [{"lcsc": "C1"}], "pushed_at": "T", "token": "secret"})
    assert resp.status == 200
    snap = store.get()
    assert snap["inventory"] == [{"lcsc": "C1"}]
    assert snap["received_at"]  # stamped
    assert "token" not in snap


def test_wrong_token_rejected(push_server):
    store, base = push_server
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/push", {"inventory": [], "pushed_at": "T", "token": "WRONG"})
    assert e.value.code == 403
    assert store.get() is None


def test_missing_token_rejected(push_server):
    store, base = push_server
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/push", {"inventory": [], "pushed_at": "T"})
    assert e.value.code == 403
