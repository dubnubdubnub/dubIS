import json
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def is_available() -> bool:
    return shutil.which("tailscale") is not None


def _run(args):
    return subprocess.run(["tailscale", *args], capture_output=True, text=True)


def is_logged_in() -> bool:
    if not is_available():
        return False
    res = _run(["status", "--json"])
    if res.returncode != 0:
        return False
    try:
        return json.loads(res.stdout).get("BackendState") == "Running"
    except (json.JSONDecodeError, AttributeError):
        return False


def serve_url() -> str:
    res = _run(["status", "--json"])
    try:
        dns = json.loads(res.stdout)["Self"]["DNSName"].rstrip(".")
        return f"https://{dns}"
    except (json.JSONDecodeError, KeyError):
        return ""


def enable_serve(read_port: int) -> str:
    if not is_available():
        raise RuntimeError("tailscale CLI not found on PATH")
    if not is_logged_in():
        raise RuntimeError("tailscale is not logged in (run: tailscale up)")
    res = _run(["serve", "--bg", "--https=443", f"http://127.0.0.1:{read_port}"])
    if res.returncode != 0:
        raise RuntimeError(f"tailscale serve failed: {res.stderr.strip()}")
    return serve_url()


def self_login() -> str:
    """Return the Tailscale login name of this machine's own user, or "" on any failure."""
    if not is_available():
        return ""
    res = _run(["status", "--json"])
    if res.returncode != 0:
        return ""
    try:
        data = json.loads(res.stdout)
        user_id = str(data["Self"]["UserID"])
        return data["User"][user_id]["LoginName"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""


def disable_serve() -> None:
    if is_available():
        _run(["serve", "--https=443", "off"])
