"""MCP SSH server — gives Claude native ssh_exec, ssh_read_file, ssh_write_file,
scp_download, scp_upload, and openpnp_api tools."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

import paramiko
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ssh")

# ── Connection pool ──────────────────────────────────────────

_clients: dict[str, paramiko.SSHClient] = {}
_ssh_config: paramiko.SSHConfig | None = None

OPENPNP_BASE_URL = "http://100.96.249.60:8899"
DEFAULT_TIMEOUT = 30


def _load_ssh_config() -> paramiko.SSHConfig:
    global _ssh_config
    if _ssh_config is None:
        _ssh_config = paramiko.SSHConfig()
        config_path = Path.home() / ".ssh" / "config"
        if config_path.exists():
            with open(config_path) as f:
                _ssh_config.parse(f)
    return _ssh_config


def _get_client(host: str) -> paramiko.SSHClient:
    """Get or create a persistent SSH connection for a host."""
    if host in _clients:
        # Check if connection is still alive
        transport = _clients[host].get_transport()
        if transport is not None and transport.is_active():
            return _clients[host]
        # Dead connection, remove it
        try:
            _clients[host].close()
        except Exception:
            pass
        del _clients[host]

    cfg = _load_ssh_config()
    host_cfg = cfg.lookup(host)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    hostname = host_cfg.get("hostname", host)
    username = host_cfg.get("user")
    key_filename = host_cfg.get("identityfile")
    if key_filename:
        # paramiko returns a list; expand ~ in each path
        key_filename = [os.path.expanduser(k) for k in key_filename]

    client.connect(
        hostname=hostname,
        username=username,
        key_filename=key_filename,
        timeout=DEFAULT_TIMEOUT,
    )
    _clients[host] = client
    return client


# ── SSH tools ────────────────────────────────────────────────


@mcp.tool()
def ssh_exec(host: str, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute a command on a remote host via SSH.

    Args:
        host: SSH host alias (e.g. 'pnp', 'ux430')
        command: Shell command to execute
        timeout: Command timeout in seconds (default 30)

    Returns:
        JSON with exit_code, stdout, stderr
    """
    client = _get_client(host)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    return json.dumps({
        "exit_code": exit_code,
        "stdout": stdout.read().decode("utf-8", errors="replace"),
        "stderr": stderr.read().decode("utf-8", errors="replace"),
    })


@mcp.tool()
def ssh_read_file(host: str, path: str) -> str:
    """Read a file from a remote host via SFTP.

    Args:
        host: SSH host alias (e.g. 'pnp', 'ux430')
        path: Absolute path to the file on the remote host

    Returns:
        File contents as string
    """
    client = _get_client(host)
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "r") as f:
            return f.read().decode("utf-8", errors="replace")
    finally:
        sftp.close()


@mcp.tool()
def ssh_write_file(host: str, path: str, content: str) -> str:
    """Write content to a file on a remote host via SFTP.

    Args:
        host: SSH host alias (e.g. 'pnp', 'ux430')
        path: Absolute path to the file on the remote host
        content: File contents to write

    Returns:
        JSON with success status
    """
    client = _get_client(host)
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "w") as f:
            f.write(content.encode("utf-8"))
        return json.dumps({"ok": True, "path": path})
    finally:
        sftp.close()


@mcp.tool()
def scp_download(host: str, remote_path: str, local_path: str) -> str:
    """Download a file from a remote host via SFTP.

    Args:
        host: SSH host alias (e.g. 'pnp', 'ux430')
        remote_path: Absolute path to the file on the remote host
        local_path: Local path to save the downloaded file

    Returns:
        JSON with local file path
    """
    client = _get_client(host)
    sftp = client.open_sftp()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        sftp.get(remote_path, local_path)
        return json.dumps({"ok": True, "local_path": os.path.abspath(local_path)})
    finally:
        sftp.close()


@mcp.tool()
def scp_upload(host: str, local_path: str, remote_path: str) -> str:
    """Upload a local file to a remote host via SFTP.

    Args:
        host: SSH host alias (e.g. 'pnp', 'ux430')
        local_path: Local path of the file to upload
        remote_path: Absolute path on the remote host to save to

    Returns:
        JSON with remote file path
    """
    client = _get_client(host)
    sftp = client.open_sftp()
    try:
        sftp.put(local_path, remote_path)
        return json.dumps({"ok": True, "remote_path": remote_path})
    finally:
        sftp.close()


# ── OpenPnP HTTP bridge tool ────────────────────────────────


@mcp.tool()
def openpnp_api(method: str, path: str, body: str | None = None) -> str:
    """Call the OpenPnP HTTP bridge running on the PnP machine.

    The bridge exposes live machine state, camera frames, parts, feeders, etc.
    at http://100.96.249.60:8899.

    Args:
        method: HTTP method (GET or POST)
        path: API path (e.g. '/api/state', '/api/camera', '/api/parts')
        body: Optional JSON body for POST requests

    Returns:
        Response body (JSON string or base64-encoded binary for images)
    """
    import base64

    url = OPENPNP_BASE_URL + path
    data = body.encode("utf-8") if body else None
    headers = {"Content-Type": "application/json"} if data else {}

    req = Request(url, data=data, headers=headers, method=method)
    try:
        resp = urlopen(req, timeout=DEFAULT_TIMEOUT)
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read()

        if "image/" in content_type:
            return json.dumps({
                "ok": True,
                "content_type": content_type,
                "data_base64": base64.b64encode(raw).decode("ascii"),
            })
        else:
            return raw.decode("utf-8", errors="replace")
    except URLError as e:
        raise RuntimeError(f"OpenPnP bridge request failed: {e}")


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
