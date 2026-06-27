"""Tests for app.pyw mirror hooks (startup/shutdown push)."""

from unittest import mock

from tests.python.helpers import make_api


def test_push_event_startup_and_shutdown_are_gated(tmp_path):
    """Verify startup/shutdown push_event calls are gated (no-op when disabled)."""
    api = make_api(tmp_path)
    # Disabled by default → push_event must be a no-op (no exception, no push).
    with mock.patch("mirror_push.push_snapshot") as ps:
        api._mirror_ctl.push_event(api._load_organized(), dubis_running=True, block=True)
        api._mirror_ctl.push_event(api._load_organized(), dubis_running=False, block=True)
        ps.assert_not_called()
