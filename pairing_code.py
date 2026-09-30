"""The `.<port>` suffix dubIS adds to a pairing code, and removing it again.

A pairing route answers `<nonce>.<port>` (`server/routes/distributors.py`
`_with_port`), and the bridge extension sends the session to 127.0.0.1 on that
port. The nonce stores hold the bare nonce, so they strip the suffix before
checking. A nonce is base64url, which has no `.`, so the suffix is unambiguous.
"""

from __future__ import annotations

import re

_PORT_SUFFIX = re.compile(r"\.\d{1,5}$")


def strip_code_port(code: str) -> str:
    """`abc.55200` -> `abc`. A code without a port suffix comes back unchanged."""
    return _PORT_SUFFIX.sub("", code or "")
