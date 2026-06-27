"""Golden snapshot tests for pnp_server.py capture-page HTML generators.

These tests pin the exact byte output (via SHA-256) of _capture_page_html and
_expired_page_html to the pre-split baseline.  If a refactor accidentally changes
the generated HTML the digest will drift and the test will fail, proving the
change is NOT byte-identical.

Expected hashes were computed from the unmodified pnp_server.py before any
decomposition work began.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from pnp_server import _capture_page_html, _expired_page_html


def test_capture_page_html_lcsc():
    result = _capture_page_html('lcsc', 'test-session-abc')
    assert hashlib.sha256(result.encode()).hexdigest() == \
        '0afb1459ed68770aea2fca865f73c2987f1184b0f228c217d6b1f8c82ae0caac'


def test_capture_page_html_special_chars():
    """Exercises html.escape + json.dumps paths with special characters."""
    result = _capture_page_html('generic & <co>', 's2')
    assert hashlib.sha256(result.encode()).hexdigest() == \
        '708e9fb5951948b687fde22f959fc186f5f94091eb4d99e8e20e4ef80de90291'


def test_expired_page_html():
    result = _expired_page_html()
    assert hashlib.sha256(result.encode()).hexdigest() == \
        '69db84ef1472dd13347fac22010a8e3e025a891ccf207403c79e0a1c99707258'
