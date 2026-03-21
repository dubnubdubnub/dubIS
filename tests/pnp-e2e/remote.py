"""SSH helper for cross-compute PnP E2E tests.

Deploys OpenPnP config to a remote host, launches OpenPnP via SSH,
and waits for it to finish. Uses paramiko with ~/.ssh/config for
connection settings.

Reuses the SSH config pattern from tools/ssh-mcp/server.py.
"""

import os
import stat
import time
from pathlib import Path

import paramiko

DEFAULT_TIMEOUT = 120


# ── SSH connection ───────────────────────────────────────────

_ssh_config: paramiko.SSHConfig | None = None


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
    """Create an SSH connection to a host using ~/.ssh/config."""
    cfg = _load_ssh_config()
    host_cfg = cfg.lookup(host)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    hostname = host_cfg.get("hostname", host)
    username = host_cfg.get("user")
    key_filename = host_cfg.get("identityfile")
    if key_filename:
        key_filename = [os.path.expanduser(k) for k in key_filename]

    client.connect(
        hostname=hostname,
        username=username,
        key_filename=key_filename,
        timeout=30,
    )
    return client


# ── Config deployment ────────────────────────────────────────


def deploy_config(host: str, config_dir: str, jython_script: str,
                  dubis_url: str) -> str | None:
    """Deploy OpenPnP test config to remote ~/.openpnp2/ via SFTP.

    Returns the path to the backup directory on the remote host (if existing
    config was backed up), or None.
    """
    client = _get_client(host)
    sftp = client.open_sftp()

    remote_openpnp = _remote_home(sftp) + "/.openpnp2"

    # Back up existing config if machine.xml exists
    backup_dir = None
    try:
        sftp.stat(remote_openpnp + "/machine.xml")
        backup_dir = _remote_home(sftp) + "/.openpnp2-backup-e2e"
        _sftp_exec(client, f"rm -rf {backup_dir} && cp -a {remote_openpnp} {backup_dir}")
    except FileNotFoundError:
        pass

    # Clean the remote directory completely and recreate it.
    # This prevents stale event scripts from crashing ScriptFileWatcher
    # and stale logs from misleading diagnostics.
    _sftp_exec(client, f"rm -rf {remote_openpnp}")
    _sftp_mkdir_p(sftp, remote_openpnp)

    # Upload test config recursively
    _sftp_put_recursive(sftp, config_dir, remote_openpnp)

    # Upload and patch the Jython script
    events_dir = remote_openpnp + "/scripts/Events"
    _sftp_mkdir_p(sftp, events_dir)
    remote_script = events_dir + "/Job.Placement.Complete.py"
    sftp.put(jython_script, remote_script)

    # Patch DUBIS_URL in the remote script
    with sftp.open(remote_script, "r") as f:
        content = f.read().decode("utf-8")
    content = content.replace(
        'DUBIS_URL = os.environ.get("DUBIS_URL", "http://127.0.0.1:7890")',
        f'DUBIS_URL = "{dubis_url}"',
    )
    with sftp.open(remote_script, "w") as f:
        f.write(content.encode("utf-8"))

    sftp.close()
    client.close()
    return backup_dir


def launch_openpnp(host: str, openpnp_bin: str = "/usr/local/bin/openpnp.sh",
                   job_path: str | None = None,
                   timeout: int = DEFAULT_TIMEOUT) -> tuple[paramiko.SSHClient, paramiko.Channel]:
    """Launch OpenPnP on the remote host via SSH.

    Returns (client, channel) so the caller can wait for exit.
    """
    client = _get_client(host)

    # Build environment variables
    env_parts = []
    if job_path:
        # Use $HOME instead of ~ for shell expansion inside env var assignment
        expanded_path = job_path.replace("~", "$HOME")
        env_parts.append(f'OPENPNP_TEST_JOB="{expanded_path}"')

    # Detect remote platform and set up display
    _, stdout, _ = client.exec_command("uname -s", timeout=10)
    platform = stdout.read().decode().strip().lower()

    if platform == "linux":
        # Kill any stale Xvfb or OpenPnP from previous runs (ignore errors)
        _, _, stderr = client.exec_command(
            "pkill -9 -f Xvfb 2>/dev/null; pkill -9 -f openpnp 2>/dev/null; sleep 1",
            timeout=15,
        )
        try:
            stderr.read()  # drain to avoid paramiko warnings
        except Exception:
            pass

        # Start Xvfb on a unique display (fire-and-forget with nohup)
        display = ":42"
        _, xout, _ = client.exec_command(
            f"nohup Xvfb {display} -screen 0 1024x768x24 -nolisten tcp "
            f">/dev/null 2>&1 & sleep 2 && echo OK",
            timeout=15,
        )
        try:
            result = xout.read(100).decode()
            if "OK" not in result:
                print(f"[remote] WARN: Xvfb startup uncertain: {result!r}")
        except Exception:
            print("[remote] WARN: Xvfb startup timed out")
        env_parts.append(f"DISPLAY={display}")

    if platform == "darwin":
        # macOS SSH sessions can't access WindowServer (Java gets HeadlessException).
        # Launch via a temporary launchd job in the user's GUI domain instead.
        cmd = _build_macos_gui_launch_cmd(openpnp_bin, env_parts, timeout)
    else:
        env_str = " ".join(env_parts) + " " if env_parts else ""
        cmd = f"{env_str}{openpnp_bin}"

    print(f"[remote] Launching on {platform}: {cmd[:200]}")

    transport = client.get_transport()
    channel = transport.open_session()
    channel.settimeout(timeout)
    channel.exec_command(cmd)

    return client, channel


_LAUNCHD_LABEL = "org.openpnp.e2e-test"


def _build_macos_gui_launch_cmd(openpnp_bin: str, env_parts: list[str],
                                timeout: int) -> str:
    """Build a shell command that launches OpenPnP via launchd in the GUI domain.

    macOS SSH sessions don't have WindowServer access, so Java AWT throws
    HeadlessException.  By submitting a temporary launchd job to gui/<uid>,
    the process inherits the logged-in user's Aqua session.

    The returned command is meant to be run over SSH.  It:
      1. Writes a wrapper script and plist to /tmp
      2. Bootstraps the job into the GUI domain
      3. Polls for completion (exit-code sentinel file)
      4. Streams stdout/stderr back through the SSH channel
      5. Cleans up the launchd job
    """
    # Build the wrapper script content
    wrapper_lines = ["#!/bin/bash"]
    for ep in env_parts:
        wrapper_lines.append(f"export {ep}")
    wrapper_lines.append(f"{openpnp_bin}")
    wrapper_lines.append("echo $? > /tmp/openpnp-e2e-exit")
    wrapper_body = "\n".join(wrapper_lines)

    plist_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/tmp/openpnp-e2e-wrapper.sh</string>
    </array>
    <key>StandardOutPath</key>
    <string>/tmp/openpnp-e2e-out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/openpnp-e2e-err.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>"""

    # Shell script that sets up, runs, waits, and streams output
    return f"""set -e
# Write wrapper script
cat > /tmp/openpnp-e2e-wrapper.sh << 'WRAPPER_EOF'
{wrapper_body}
WRAPPER_EOF
chmod +x /tmp/openpnp-e2e-wrapper.sh

# Write plist
cat > /tmp/openpnp-e2e.plist << 'PLIST_EOF'
{plist_xml}
PLIST_EOF

# Clean up any previous run
rm -f /tmp/openpnp-e2e-exit /tmp/openpnp-e2e-out.log /tmp/openpnp-e2e-err.log
launchctl bootout gui/$(id -u)/{_LAUNCHD_LABEL} 2>/dev/null || true

# Bootstrap the job into the GUI domain (runs immediately due to RunAtLoad)
launchctl bootstrap gui/$(id -u) /tmp/openpnp-e2e.plist

# Poll for completion (wrapper writes exit code to sentinel file)
ELAPSED=0
while [ ! -f /tmp/openpnp-e2e-exit ] && [ $ELAPSED -lt {timeout} ]; do
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

# Stream output
cat /tmp/openpnp-e2e-out.log 2>/dev/null || true
cat /tmp/openpnp-e2e-err.log >&2 2>/dev/null || true

# Clean up launchd job
launchctl bootout gui/$(id -u)/{_LAUNCHD_LABEL} 2>/dev/null || true

# Exit with OpenPnP's exit code (or 1 if timed out)
if [ -f /tmp/openpnp-e2e-exit ]; then
    exit $(cat /tmp/openpnp-e2e-exit)
else
    echo "TIMEOUT: OpenPnP did not finish within {timeout}s" >&2
    exit 1
fi"""


def wait_for_exit(channel: paramiko.Channel, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """Wait for the remote OpenPnP process to finish.

    Returns (exit_code, output).
    """
    channel.settimeout(timeout)
    output_chunks = []
    try:
        while True:
            if channel.recv_ready():
                output_chunks.append(channel.recv(4096).decode("utf-8", errors="replace"))
            if channel.recv_stderr_ready():
                output_chunks.append(channel.recv_stderr(4096).decode("utf-8", errors="replace"))
            if channel.exit_status_ready():
                # Drain remaining output
                while channel.recv_ready():
                    output_chunks.append(channel.recv(4096).decode("utf-8", errors="replace"))
                while channel.recv_stderr_ready():
                    output_chunks.append(channel.recv_stderr(4096).decode("utf-8", errors="replace"))
                break
            time.sleep(0.5)
    except Exception:
        pass

    exit_code = channel.recv_exit_status()
    return exit_code, "".join(output_chunks)


def dismiss_dialog_remote(host: str):
    """Send Enter keys on the remote host to dismiss OpenPnP first-run dialog.

    Tries polling for the dialog window before sending keys.
    """
    time.sleep(8)
    client = _get_client(host)

    # Detect platform
    _, stdout, _ = client.exec_command("uname -s", timeout=10)
    platform = stdout.read().decode().strip().lower()

    for _ in range(5):
        if platform == "linux":
            client.exec_command(
                'DISPLAY=:42 xdotool search --name "Welcome" key Return 2>/dev/null || '
                'DISPLAY=:42 xdotool key Return 2>/dev/null',
                timeout=10,
            )
        elif platform == "darwin":
            client.exec_command(
                'osascript -e \'tell application "System Events" to keystroke return\'',
                timeout=10,
            )
        time.sleep(1)

    client.close()


def read_remote_log(host: str) -> str:
    """Read the newest OpenPnP log file from the remote host."""
    client = _get_client(host)
    try:
        _, stdout, _ = client.exec_command(
            "ls -t ~/.openpnp2/log/OpenPnP*.log 2>/dev/null | head -1",
            timeout=10,
        )
        log_path = stdout.read().decode().strip()
        if not log_path:
            return ""
        _, stdout, _ = client.exec_command(f"cat {log_path}", timeout=10)
        return stdout.read().decode(errors="replace")
    except Exception as e:
        return f"(error reading remote log: {e})"
    finally:
        client.close()


def cleanup(host: str, backup_dir: str | None):
    """Restore original OpenPnP config and kill test processes on the remote host."""
    client = _get_client(host)
    try:
        # Kill Xvfb started by the test (Linux)
        client.exec_command("pkill -9 -f 'Xvfb :42' 2>/dev/null; true", timeout=10)
        # Clean up launchd job if it exists (macOS)
        client.exec_command(
            f"launchctl bootout gui/$(id -u)/{_LAUNCHD_LABEL} 2>/dev/null; "
            "rm -f /tmp/openpnp-e2e-wrapper.sh /tmp/openpnp-e2e.plist "
            "/tmp/openpnp-e2e-exit /tmp/openpnp-e2e-out.log /tmp/openpnp-e2e-err.log; true",
            timeout=10,
        )
        if backup_dir:
            remote_openpnp = backup_dir.replace("-backup-e2e", "")
            _sftp_exec(client, f"rm -rf {remote_openpnp} && mv {backup_dir} {remote_openpnp}")
    finally:
        client.close()


# ── SFTP utilities ───────────────────────────────────────────


def _remote_home(sftp: paramiko.SFTPClient) -> str:
    """Get the remote home directory."""
    return sftp.normalize(".")


def _sftp_exec(client: paramiko.SSHClient, cmd: str):
    """Execute a command and raise on failure."""
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        err = stderr.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Remote command failed (exit {exit_code}): {cmd}\n{err}")


def _sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_path: str):
    """Recursively create directories on the remote host."""
    parts = remote_path.split("/")
    current = ""
    for part in parts:
        if not part:
            current = "/"
            continue
        current = current + "/" + part if current != "/" else "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def _sftp_put_recursive(sftp: paramiko.SFTPClient, local_dir: str, remote_dir: str):
    """Recursively upload a local directory to a remote path."""
    for item in os.listdir(local_dir):
        local_path = os.path.join(local_dir, item)
        remote_path = remote_dir + "/" + item
        if os.path.isfile(local_path):
            sftp.put(local_path, remote_path)
        elif os.path.isdir(local_path):
            _sftp_mkdir_p(sftp, remote_path)
            _sftp_put_recursive(sftp, local_path, remote_path)
