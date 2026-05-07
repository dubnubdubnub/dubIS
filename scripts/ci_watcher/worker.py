"""Single-concurrency worker that drives `claude -p` per CI failure event.

Reads events off a Unix datagram socket, fetches the triage payload via gh,
spawns claude -p with the prompt and payload, retries once on subprocess
failure, and logs everything to stderr (which launchd routes to /var/log).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.ci_watcher.triage_payload import build_payload

logger = logging.getLogger("ci-watcher.worker")


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


_TIMEOUT_SECONDS = 5 * 60


def process_event(event: dict[str, Any], *, prompt_path: Path, cwd: Path) -> ProcessResult:
    """Build payload, spawn claude -p, retry once on non-zero exit."""
    run_id = event["run_id"]
    pr = event.get("pr")

    payload = build_payload(run_id=run_id, pr=pr)
    payload_json = json.dumps(payload, separators=(",", ":"))
    prompt = prompt_path.read_text(encoding="utf-8")

    cmd = ["claude", "-p", prompt, "--append-system-prompt", payload_json]

    start = time.time()
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        logger.warning(
            "claude -p failed (run_id=%s, code=%s); retrying in 30s. stderr=%s",
            run_id, result.returncode, result.stderr[:500],
        )
        time.sleep(30)
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS
        )

    duration = time.time() - start
    logger.info("process_event run_id=%s code=%s dur=%.1fs", run_id, result.returncode, duration)
    return ProcessResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def run_worker_once(sock: socket.socket, *, prompt_path: Path, cwd: Path) -> None:
    """Process a single event from the socket. Returns when one is handled."""
    sock.settimeout(None)
    data, _ = sock.recvfrom(65536)
    try:
        event = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("malformed event on socket: %s", exc)
        return
    try:
        process_event(event, prompt_path=prompt_path, cwd=cwd)
    except subprocess.TimeoutExpired:
        logger.error("claude -p timed out for run_id=%s", event.get("run_id"))
    except Exception:
        logger.exception("worker failed handling run_id=%s", event.get("run_id"))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    socket_path = Path(os.environ.get("CI_WATCHER_SOCKET", "/var/run/ci-watcher.sock"))
    prompt_path = Path(os.environ.get(
        "CI_WATCHER_PROMPT",
        "/var/lib/ci-watcher/repo/scripts/ci_watcher/triage-prompt.md",
    ))
    cwd = Path(os.environ.get("CI_WATCHER_REPO", "/var/lib/ci-watcher/repo"))

    if socket_path.exists():
        socket_path.unlink()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(socket_path))
    os.chmod(socket_path, 0o660)

    logger.info("worker ready, listening on %s", socket_path)
    try:
        while True:
            run_worker_once(sock, prompt_path=prompt_path, cwd=cwd)
    finally:
        sock.close()
        if socket_path.exists():
            socket_path.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
