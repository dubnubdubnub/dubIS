"""A stand-in for OpenSSH's `ssh -N -L 127.0.0.1:<port>:<target> dest`.

Used by tests/python/server/test_ssh_tunnel.py via `TunnelManager(ssh_command=
[sys.executable, <this file>])`. It parses the `-L` spec out of the real argv
the hub builds and behaves according to `FAKE_SSH_MODE`:

  ok           listen on the local port and answer like a dubIS server
  die          like ok, but exit 255 after FAKE_SSH_LIFETIME seconds
  denied       print ssh's BatchMode auth failure and exit 255
  dns          print ssh's resolve failure and exit 255
  hang         never listen, never exit (a start timeout)
  open_failed  listen, but refuse every channel the way sshd does when
               nothing is at the remote target

Every invocation appends its argv (JSON, one line) to FAKE_SSH_LOG if set.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

argv = sys.argv[1:]
log = os.environ.get("FAKE_SSH_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(argv) + "\n")

spec = argv[argv.index("-L") + 1]
bind_host, port_text, target = spec.split(":", 2)
port = int(port_text)
dest = argv[-1]
mode = os.environ.get("FAKE_SSH_MODE", "ok")


def fail(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    sys.exit(255)


if mode == "denied":
    fail(f"{dest}: Permission denied (publickey).")
if mode == "dns":
    fail(f"ssh: Could not resolve hostname {dest.split('@')[-1]}: "
         "nodename nor servname provided, or not known")
if mode == "hang":
    time.sleep(3600)
    sys.exit(0)

if mode == "open_failed":
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((bind_host, port))
    except OSError:
        fail(f"bind [{bind_host}]:{port}: Address already in use")
    srv.listen(8)
    n = 0
    while True:
        conn, _ = srv.accept()
        n += 1
        sys.stderr.write(f"channel {n}: open failed: connect failed: No such file or directory\n")
        sys.stderr.flush()
        conn.close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/v1/health"):
            body = {"ok": True}
        else:
            body = {"path": self.path, "target": target, "dest": dest,
                    "host_header": self.headers.get("Host", "")}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence
        pass


try:
    server = ThreadingHTTPServer((bind_host, port), Handler)
except OSError:
    fail(f"bind [{bind_host}]:{port}: Address already in use")
threading.Thread(target=server.serve_forever, daemon=True).start()

if mode == "die":
    time.sleep(float(os.environ.get("FAKE_SSH_LIFETIME", "0.5")))
    server.shutdown()
    server.server_close()
    fail("Timeout, server not responding.")
while True:
    time.sleep(3600)
