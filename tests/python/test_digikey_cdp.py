"""Tests for digikey_cdp — CDP WebSocket cookie extraction."""

import http.client
import json
from unittest.mock import MagicMock, patch

import pytest

from digikey_cdp import cdp_get_cookies


class TestCdpGetCookies:
    def test_connection_refused_raises(self):
        """Connecting to a port with nothing listening raises ConnectionRefusedError."""
        # Use a port that is almost certainly not listening
        with pytest.raises((ConnectionRefusedError, OSError)):
            cdp_get_cookies(19199)

    def test_no_page_target_raises(self):
        """When CDP returns targets but none are pages, raises RuntimeError."""
        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps([
            {"type": "service_worker", "url": "chrome://sw"},
        ]).encode()

        fake_conn = MagicMock(spec=http.client.HTTPConnection)
        fake_conn.getresponse.return_value = fake_response

        with patch("http.client.HTTPConnection", return_value=fake_conn):
            with pytest.raises(RuntimeError, match="No page target"):
                cdp_get_cookies(19200)

    def test_no_ws_debugger_url_raises(self):
        """Page target without webSocketDebuggerUrl raises RuntimeError."""
        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps([
            {"type": "page", "url": "https://digikey.com"},
        ]).encode()

        fake_conn = MagicMock(spec=http.client.HTTPConnection)
        fake_conn.getresponse.return_value = fake_response

        with patch("http.client.HTTPConnection", return_value=fake_conn):
            with pytest.raises(RuntimeError, match="No page target"):
                cdp_get_cookies(19200)

    def test_empty_targets_raises(self):
        """Empty target list raises RuntimeError."""
        fake_response = MagicMock()
        fake_response.read.return_value = b"[]"

        fake_conn = MagicMock(spec=http.client.HTTPConnection)
        fake_conn.getresponse.return_value = fake_response

        with patch("http.client.HTTPConnection", return_value=fake_conn):
            with pytest.raises(RuntimeError, match="No page target"):
                cdp_get_cookies(19200)
