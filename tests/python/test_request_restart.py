"""`InventoryApi.request_restart` — flag the intent, then close as if the user had."""

import inventory_api


def _api():
    return inventory_api.InventoryApi()


def test_a_fresh_api_has_no_pending_restart():
    assert _api()._restart_pending is False


def test_request_restart_flags_the_intent_and_force_closes(monkeypatch):
    """Reuses the ordinary close path rather than spawning here, so _cleanup()
    still runs first and releases the data-dir lock before anything replaces us."""
    api = _api()
    calls = []
    monkeypatch.setattr(api, "confirm_close", lambda: calls.append("closed"))
    api.request_restart()
    assert api._restart_pending is True
    assert calls == ["closed"]


def test_the_restart_force_closes_rather_than_prompting(monkeypatch):
    """The user asked for a restart, so a BOM-dirty prompt would be answering a
    question they did not ask; their preference edit is already persisted."""
    api = _api()
    api._bom_dirty = True
    monkeypatch.setattr("webview.windows", [], raising=False)
    api.request_restart()
    assert api._force_close is True


def test_restarting_twice_is_harmless(monkeypatch):
    api = _api()
    monkeypatch.setattr("webview.windows", [], raising=False)
    api.request_restart()
    api.request_restart()
    assert api._restart_pending is True
