"""CDP WebSocket cookie extraction for Digikey browser sessions."""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import socket
import struct

logger = logging.getLogger(__name__)


def cdp_get_cookies(port: int) -> list[dict]:
    """Read digikey.com cookies via Chrome DevTools Protocol.

    Implements a minimal WebSocket client (no external deps) to send a
    single CDP command and read the response.
    """
    # 1. Get a page target's WebSocket URL (cookies need page context)
    conn = http.client.HTTPConnection("localhost", port, timeout=2)
    conn.request("GET", "/json")
    targets = json.loads(conn.getresponse().read())
    conn.close()
    # Prefer a digikey tab; fall back to any page target
    page = None
    for t in targets:
        if t.get("type") == "page":
            if page is None:
                page = t
            if "digikey" in t.get("url", "").lower():
                page = t
                break
    if not page or "webSocketDebuggerUrl" not in page:
        raise RuntimeError(f"No page target found ({len(targets)} targets)")
    ws_path = "/" + page["webSocketDebuggerUrl"].split("/", 3)[3]

    # 2. WebSocket handshake
    sock = socket.create_connection(("localhost", port), timeout=2)
    ws_key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        f"GET {ws_path} HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(4096)
    if b"101" not in buf.split(b"\r\n")[0]:
        sock.close()
        raise RuntimeError("WebSocket upgrade failed")

    # 3. Send Network.getAllCookies on the page target
    cmd = json.dumps({
        "id": 1,
        "method": "Network.getAllCookies",
    }).encode()
    mask = os.urandom(4)
    hdr = bytes([0x81])  # FIN + text opcode
    if len(cmd) < 126:
        hdr += bytes([0x80 | len(cmd)])
    else:
        hdr += bytes([0x80 | 126]) + struct.pack(">H", len(cmd))
    hdr += mask
    sock.sendall(hdr + bytes(b ^ mask[i % 4] for i, b in enumerate(cmd)))

    # 4. Read frames until we get our response (id=1)
    def _recv(n):
        d = b""
        while len(d) < n:
            c = sock.recv(n - len(d))
            if not c:
                raise RuntimeError("CDP connection closed")
            d += c
        return d

    try:
        for _ in range(50):  # safety limit
            h = _recv(2)
            plen = h[1] & 0x7F
            if plen == 126:
                plen = struct.unpack(">H", _recv(2))[0]
            elif plen == 127:
                plen = struct.unpack(">Q", _recv(8))[0]
            payload = _recv(plen)
            try:
                msg = json.loads(payload)
                if msg.get("id") == 1:
                    return msg.get("result", {}).get("cookies", [])
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    finally:
        sock.close()
    raise RuntimeError("No CDP response received")
