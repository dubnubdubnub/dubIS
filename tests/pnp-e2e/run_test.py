"""PnP E2E test orchestrator.

Runs on self-hosted runners (ux430 Linux, m4-air macOS) with OpenPnP installed.
Starts headless dubIS, launches OpenPnP with a null driver, and verifies that
placement events correctly decrement inventory.

Modes:
  Same-machine (default): dubIS + OpenPnP on the same host.
  Cross-compute (--remote-openpnp <host>): dubIS local, OpenPnP on remote host.

On Linux: uses Xvfb for headless display, xdotool to dismiss dialogs.
On macOS: uses the native display (always-on), osascript to dismiss dialogs.

Requirements on the host:
  - OpenPnP installed (openpnp.sh on PATH or OPENPNP_BIN env var)
  - Linux: Xvfb, xdotool (apt install xvfb xdotool)
  - Python 3.12+ with dubIS dependencies (pip install -r requirements-dev.txt)
  - Cross-compute: paramiko, SSH config with host alias for remote
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

DUBIS_PORT = int(os.environ.get("DUBIS_PORT", "7890"))
TIMEOUT_SECONDS = 120
OPENPNP_MAX_RETRIES = 2  # Retry OpenPnP on hang (macOS NullDriver flake)
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")
OPENPNP_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "openpnp-config")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# All 3 OpenPnP test parts mapped to real LCSC parts in fixtures.
# Board has: 3x R100k, 2x C100nF, 1x SMD_RES_100k → 6 total placements.
PART_MAP = {
    "SMD_RES_100k": "C25764",
    "R100k": "C25794",
    "C100nF": "C440198",
}

# Expected quantity decreases per mapped LCSC part.
EXPECTED_DECREASES = {
    "C25794": 3,    # 3x R100k placements
    "C440198": 2,   # 2x C100nF placements
    "C25764": 1,    # 1x SMD_RES_100k placement
}


def _http_get(url):
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as exc:
        print(f"[e2e] HTTP GET {url} failed: {exc}")
        return None, None


def _http_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as exc:
        print(f"[e2e] HTTP POST {url} failed: {exc}")
        return None, None


def wait_for_server(base_url, timeout=30):
    """Poll /api/health until the server is up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _http_get(f"{base_url}/api/health")
        if status == 200 and body and body.get("ok"):
            return True
        time.sleep(0.5)
    return False


def snapshot_quantities(base_url):
    """GET /api/parts and return {part_key: qty} dict."""
    status, body = _http_get(f"{base_url}/api/parts")
    if status != 200 or not body:
        raise RuntimeError(f"Failed to get parts: status={status}")
    result = {}
    for p in body["parts"]:
        key = p.get("lcsc") or p.get("mpn") or p.get("digikey")
        if key:
            result[key] = p["qty"]
    return result


def _check_display():
    """Verify that a display is available for OpenPnP's GUI.

    On Linux, xvfb-run provides a virtual display. On macOS, we need a real
    GUI session (WindowServer access). Returns True if OK, raises if not.
    """
    if sys.platform == "linux":
        return True  # xvfb-run will handle this

    if sys.platform == "darwin":
        # Fast check: is WindowServer running? Works on all macOS versions,
        # no PyObjC needed (Homebrew Python doesn't ship AppKit).
        try:
            result = subprocess.run(
                ["pgrep", "-x", "WindowServer"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Deeper check: can we connect to WindowServer via AppKit?
        try:
            result = subprocess.run(
                ["python3", "-c",
                 "from AppKit import NSApplication; NSApplication.sharedApplication()"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: WindowServer socket (Render on older macOS, Listeners on Sequoia+)
        import glob as globmod
        ws_sockets = (globmod.glob("/tmp/com.apple.launchd*/Render")
                      + globmod.glob("/tmp/com.apple.launchd*/Listeners"))
        if ws_sockets:
            return True

        raise RuntimeError(
            "macOS HeadlessException: No GUI session available. "
            "The runner must be started from a login session (LaunchAgent), "
            "not as a LaunchDaemon. Ensure the Mac has auto-login and the "
            "runner is started via ./run.sh in a Terminal window or via a "
            "LaunchAgent plist."
        )

    return True


def _get_tailscale_ip():
    """Get this machine's Tailscale IP address."""
    # Try multiple possible Tailscale binary locations
    candidates = ["tailscale"]
    if sys.platform == "darwin":
        candidates.append("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    for binary in candidates:
        try:
            result = subprocess.run(
                [binary, "ip", "-4"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError("Could not determine Tailscale IP. Is Tailscale running?")


# ── Same-machine OpenPnP helpers ─────────────────────────────


def _setup_local_openpnp(openpnp_home, dubis_port):
    """Install test config into ~/.openpnp2, return (backup_dir, job_path)."""
    openpnp_user_config = os.path.expanduser("~/.openpnp2")
    os.makedirs(openpnp_user_config, exist_ok=True)

    # Back up existing config if present
    backup_dir = None
    if os.path.exists(os.path.join(openpnp_user_config, "machine.xml")):
        backup_dir = tempfile.mkdtemp(prefix="openpnp-backup-")
        for item in os.listdir(openpnp_user_config):
            src = os.path.join(openpnp_user_config, item)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(backup_dir, item))
            elif os.path.isdir(src):
                shutil.copytree(src, os.path.join(backup_dir, item))

    # Install test config
    for item in os.listdir(openpnp_home):
        src = os.path.join(openpnp_home, item)
        dst = os.path.join(openpnp_user_config, item)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
        elif os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

    # Install the real Job.Placement.Complete.py from the project
    events_dir = os.path.join(openpnp_user_config, "scripts", "Events")
    os.makedirs(events_dir, exist_ok=True)
    shutil.copy2(
        os.path.join(PROJECT_ROOT, "openpnp", "Job.Placement.Complete.py"),
        os.path.join(events_dir, "Job.Placement.Complete.py"),
    )

    # Patch the Jython script to point at localhost
    jython_script = os.path.join(events_dir, "Job.Placement.Complete.py")
    with open(jython_script, "r") as f:
        content = f.read()
    content = content.replace(
        'DUBIS_URL = os.environ.get("DUBIS_URL", "http://127.0.0.1:7890")',
        f'DUBIS_URL = "http://127.0.0.1:{dubis_port}"',
    )
    with open(jython_script, "w") as f:
        f.write(content)

    job_path = os.path.join(openpnp_home, "jobs", "test-job.job.xml")
    return backup_dir, job_path


def _dismiss_dialog_local(xvfb_display):
    """Safety net: dismiss OpenPnP's Welcome dialog if it appears.

    The test config's machine.xml includes Welcome2_0_Dialog_Shown=true,
    which should suppress the dialog entirely. This function is a fallback
    in case the dialog appears anyway (e.g. on a fresh OpenPnP version).

    Requires matchbox-window-manager on the Xvfb display for xdotool
    to deliver key events to Java Swing windows.
    """
    time.sleep(8)
    if sys.platform == "linux":
        dismiss_env = os.environ.copy()
        dismiss_env["DISPLAY"] = xvfb_display

        # Check if Welcome dialog is showing
        try:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", "Welcome"],
                env=dismiss_env, timeout=5, capture_output=True, text=True,
            )
            welcome_ids = [w for w in result.stdout.strip().split("\n") if w]
            if not welcome_ids:
                print("[e2e] No Welcome dialog detected (suppressed by machine.xml)")
                return
            print(f"[e2e] WARN: Welcome dialog appeared despite machine.xml property!")
        except FileNotFoundError:
            print("[e2e] WARN: xdotool not found")
            return
        except Exception:
            return

        # Try to dismiss: windowactivate + key combos (needs matchbox WM)
        for wid in welcome_ids:
            for key in ["Return", "space", "Escape", "alt+F4"]:
                try:
                    subprocess.run(
                        ["xdotool", "windowactivate", "--sync", wid,
                         "key", "--clearmodifiers", key],
                        env=dismiss_env, timeout=5, capture_output=True,
                    )
                except Exception:
                    pass
                time.sleep(1)

        # Verify dismissal
        time.sleep(3)
        try:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", "Welcome"],
                env=dismiss_env, timeout=5, capture_output=True, text=True,
            )
            if not result.stdout.strip():
                print("[e2e] Welcome dialog dismissed via xdotool")
            else:
                print("[e2e] WARN: Welcome dialog still present after key attempts")
        except Exception:
            pass
    else:
        # macOS: use osascript as fallback
        for _ in range(5):
            try:
                subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to keystroke return'],
                    timeout=5, capture_output=True,
                )
            except FileNotFoundError:
                print("[e2e] WARN: osascript not found")
                break
            except Exception:
                pass
            time.sleep(2)


def _restore_local_openpnp(backup_dir):
    """Restore backed-up OpenPnP config."""
    if not backup_dir:
        return
    openpnp_user_config = os.path.expanduser("~/.openpnp2")
    shutil.rmtree(openpnp_user_config)
    shutil.copytree(backup_dir, openpnp_user_config)
    shutil.rmtree(backup_dir)


def _launch_and_wait_openpnp(openpnp_bin, env, xvfb_display, timeout):
    """Launch OpenPnP, wait for completion, return results.

    Returns (exit_code, output_text, openpnp_log_text).
    exit_code is the process exit code; 2 = watchdog killed (stall detected).
    """
    import glob as globmod
    import signal as signal_mod

    # Clean stale logs
    openpnp_log_dir = os.path.expanduser("~/.openpnp2/log")
    if os.path.isdir(openpnp_log_dir):
        for lf in globmod.glob(os.path.join(openpnp_log_dir, "OpenPnP*.log")):
            try:
                os.unlink(lf)
            except Exception:
                pass

    # Temp file for output capture
    stdout_file = tempfile.NamedTemporaryFile(
        mode="w", prefix="openpnp-out-", suffix=".log", delete=False,
    )
    stdout_path = stdout_file.name

    proc = subprocess.Popen(
        [openpnp_bin], env=env,
        stdout=stdout_file, stderr=subprocess.STDOUT,
        cwd=os.path.expanduser("~/.openpnp2"),
        start_new_session=True,
    )

    # Dialog dismissal
    dialog_thread = threading.Thread(
        target=_dismiss_dialog_local, args=(xvfb_display,), daemon=True,
    )
    dialog_thread.start()

    # Monitor output
    def _monitor(path, stop_event):
        try:
            with open(path, "r") as f:
                while not stop_event.is_set():
                    line = f.readline()
                    if line:
                        line = line.rstrip()
                        if any(kw in line for kw in ("STARTUP:", "dubIS:", "WATCHDOG:", "Error", "Exception", "INFO:")):
                            print(f"[openpnp] {line}")
                    else:
                        time.sleep(0.5)
        except Exception:
            pass

    mon_stop = threading.Event()
    threading.Thread(target=_monitor, args=(stdout_path, mon_stop), daemon=True).start()

    # Periodic diagnostics
    def _diag(proc_, display, stop_event):
        check_times = [15, 30, 60]
        prev = 0
        for check_at in check_times:
            if stop_event.wait(timeout=check_at - prev):
                return
            prev = check_at
            if proc_.poll() is not None:
                return
            if os.path.isdir(openpnp_log_dir):
                logs = globmod.glob(os.path.join(openpnp_log_dir, "OpenPnP*.log"))
                total_size = sum(os.path.getsize(f) for f in logs)
                print(f"[e2e] @{check_at}s: OpenPnP running (pid={proc_.pid}), "
                      f"log files: {len(logs)}, total size: {total_size}b")

    diag_stop = threading.Event()
    threading.Thread(target=_diag, args=(proc, xvfb_display, diag_stop), daemon=True).start()

    # Wait for completion
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal_mod.SIGKILL)
        proc.wait()

    diag_stop.set()
    mon_stop.set()
    stdout_file.close()

    with open(stdout_path, "r") as f:
        output = f.read()
    os.unlink(stdout_path)

    # Read OpenPnP log
    log_text = ""
    if os.path.isdir(openpnp_log_dir):
        log_files = sorted(
            globmod.glob(os.path.join(openpnp_log_dir, "OpenPnP*.log")),
            key=os.path.getmtime, reverse=True,
        )
        for lf in log_files:
            try:
                with open(lf, "r") as f:
                    content = f.read()
                if content.strip():
                    log_text = content
                    break
            except Exception:
                pass

    return proc.returncode, output, log_text


def _restart_dubis(dubis_proc, tmp_dir, test_source, base_url):
    """Kill and restart dubIS with fresh fixtures. Returns the new Popen."""
    if dubis_proc and dubis_proc.poll() is None:
        dubis_proc.terminate()
        try:
            dubis_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            dubis_proc.kill()
            dubis_proc.wait()

    # Re-copy fixture CSVs to reset inventory state
    for fname in ("purchase_ledger.csv", "adjustments.csv"):
        src = os.path.join(FIXTURES_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp_dir, fname))

    # Delete cache.db so dubIS rebuilds from fresh CSVs
    cache_path = os.path.join(tmp_dir, "cache.db")
    if os.path.exists(cache_path):
        os.unlink(cache_path)

    dubis_cmd = [
        sys.executable, os.path.join(os.path.dirname(__file__), "dubis_headless.py"),
        "--data-dir", tmp_dir, "--port", str(DUBIS_PORT),
        "--test-source", test_source,
    ]
    new_proc = subprocess.Popen(
        dubis_cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if not wait_for_server(base_url, timeout=15):
        raise RuntimeError("dubIS failed to restart for retry")
    return new_proc


# ── Test orchestrator ────────────────────────────────────────


def run_test(remote_openpnp=None):
    base_url = f"http://127.0.0.1:{DUBIS_PORT}"
    failures = []
    test_source = f"test:{time.strftime('%Y%m%d-%H%M%S')}"

    # Create temp data directory with fixture copies
    tmp_dir = tempfile.mkdtemp(prefix="dubis-e2e-")
    dubis_proc = None
    xvfb_proc = None
    wm_proc = None
    local_backup_dir = None
    remote_backup_dir = None

    try:
        # Copy fixture CSVs
        for fname in ("purchase_ledger.csv", "adjustments.csv"):
            src = os.path.join(FIXTURES_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp_dir, fname))

        # Write part map covering all 3 OpenPnP parts (6 placements total)
        part_map_path = os.path.join(tmp_dir, "pnp_part_map.json")
        with open(part_map_path, "w") as f:
            json.dump(PART_MAP, f)

        # ── Start headless dubIS ──
        dubis_cmd = [
            sys.executable, os.path.join(os.path.dirname(__file__), "dubis_headless.py"),
            "--data-dir", tmp_dir, "--port", str(DUBIS_PORT),
            "--test-source", test_source,
        ]
        dubis_proc = subprocess.Popen(
            dubis_cmd, cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

        print("[e2e] Waiting for dubIS server...")
        if not wait_for_server(base_url):
            dubis_proc.kill()
            output = dubis_proc.stdout.read().decode(errors="replace")
            print(f"[e2e] FAIL: dubIS server did not start.\nOutput:\n{output}")
            return 1

        # Verify consume endpoint works with a known part before OpenPnP runs
        test_part = list(PART_MAP.keys())[0]  # e.g. "SMD_RES_100k"
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": test_part, "qty": 1})
        if status == 200 and body and body.get("ok"):
            print(f"[e2e] PASS: Manual consume of {test_part} succeeded (new_qty={body.get('new_qty')})")
        else:
            print(f"[e2e] WARN: Manual consume of {test_part} failed: status={status}, body={body}")
            failures.append(f"Manual consume pre-test failed: status={status}")

        # Snapshot AFTER manual consume so OpenPnP decrements are measured cleanly
        before = snapshot_quantities(base_url)
        print(f"[e2e] Inventory snapshot (before OpenPnP): {len(before)} parts")
        for lcsc_part in EXPECTED_DECREASES:
            print(f"[e2e]   {lcsc_part}: qty={before.get(lcsc_part, 'NOT FOUND')}")

        openpnp_bin = os.environ.get("OPENPNP_BIN", "openpnp.sh")
        openpnp_home = os.environ.get("OPENPNP_CONFIG", OPENPNP_CONFIG_DIR)

        if remote_openpnp:
            # ── Cross-compute mode: OpenPnP on remote host ──
            from remote import deploy_config, launch_openpnp, wait_for_exit, dismiss_dialog_remote, read_remote_log, cleanup

            tailscale_ip = _get_tailscale_ip()
            dubis_url = f"http://{tailscale_ip}:{DUBIS_PORT}"
            jython_script = os.path.join(PROJECT_ROOT, "openpnp", "Job.Placement.Complete.py")

            print(f"[e2e] Cross-compute mode: dubIS at {dubis_url}, OpenPnP on {remote_openpnp}")

            remote_backup_dir = deploy_config(
                remote_openpnp, openpnp_home, jython_script, dubis_url,
            )
            remote_job_path = "~/.openpnp2/jobs/test-job.job.xml"

            # Launch OpenPnP remotely
            client, channel = launch_openpnp(
                remote_openpnp, openpnp_bin, job_path=remote_job_path,
                timeout=TIMEOUT_SECONDS,
            )

            # Dismiss dialog in background
            dialog_thread = threading.Thread(
                target=dismiss_dialog_remote, args=(remote_openpnp,), daemon=True,
            )
            dialog_thread.start()

            # Wait for remote OpenPnP to finish
            exit_code, openpnp_output = wait_for_exit(channel, timeout=TIMEOUT_SECONDS)
            client.close()

            print(f"[e2e] OpenPnP (remote) exited with code {exit_code}")
            # Always print output for debugging
            print(f"[e2e] OpenPnP output (last 3000 chars):\n{openpnp_output[-3000:]}")
            if exit_code != 0:
                failures.append(f"OpenPnP (remote) exited with code {exit_code}")

            # Read OpenPnP log from the remote host (not local)
            remote_log = read_remote_log(remote_openpnp)
            if remote_log:
                print(f"[e2e] Remote OpenPnP log ({len(remote_log)} bytes, last 3000 chars):")
                print(remote_log[-3000:])
            else:
                print("[e2e] WARN: No OpenPnP log found on remote host")

        else:
            # ── Same-machine mode ──
            _check_display()  # Fail fast if no GUI session available
            local_backup_dir, job_path = _setup_local_openpnp(openpnp_home, DUBIS_PORT)

            env = os.environ.copy()
            env["OPENPNP_TEST_JOB"] = os.path.abspath(job_path)

            # On Linux, start Xvfb for headless display.
            xvfb_proc = None
            wm_proc = None
            xvfb_display = None
            if sys.platform == "linux":
                import random
                subprocess.run(["pkill", "-f", "Xvfb"], capture_output=True)
                subprocess.run(["pkill", "-f", "matchbox-window-manager"], capture_output=True)
                time.sleep(0.5)

                for _ in range(5):
                    display_num = random.randint(50, 200)
                    xvfb_display = f":{display_num}"
                    xvfb_proc = subprocess.Popen(
                        ["Xvfb", xvfb_display, "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    )
                    time.sleep(1)
                    if xvfb_proc.poll() is None:
                        break
                    stderr = xvfb_proc.stderr.read().decode(errors="replace")
                    print(f"[e2e] Xvfb {xvfb_display} failed: {stderr.strip()}")
                    xvfb_proc = None

                if xvfb_proc is None:
                    raise RuntimeError("Could not start Xvfb on any display")

                env["DISPLAY"] = xvfb_display
                print(f"[e2e] Xvfb started on {xvfb_display}")

                try:
                    wm_proc = subprocess.Popen(
                        ["matchbox-window-manager", "-use_titlebar", "no"],
                        env={"DISPLAY": xvfb_display},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    time.sleep(0.5)
                    if wm_proc.poll() is None:
                        print("[e2e] matchbox-window-manager started")
                    else:
                        wm_proc = None
                except FileNotFoundError:
                    wm_proc = None

                # Ensure X11 socket is ready before launching OpenPnP
                if wm_proc is None:
                    x_socket = f"/tmp/.X11-unix/X{display_num}"
                    for _ in range(10):
                        if os.path.exists(x_socket):
                            break
                        time.sleep(0.5)
                    if os.path.exists(x_socket):
                        print(f"[e2e] X11 socket ready: {x_socket}")
                    else:
                        print(f"[e2e] WARN: X11 socket {x_socket} not found after 5s")

            # Verify scripts are installed
            events_dir = os.path.expanduser("~/.openpnp2/scripts/Events")
            if os.path.isdir(events_dir):
                scripts = os.listdir(events_dir)
                print(f"[e2e] Scripts in Events dir: {scripts}")
            else:
                print("[e2e] WARN: No Events directory found!")

            # Retry loop for OpenPnP (handles macOS NullDriver hang flake)
            openpnp_exit = None
            openpnp_output = ""
            openpnp_log = ""
            for attempt in range(1, OPENPNP_MAX_RETRIES + 1):
                if attempt > 1:
                    print(f"\n[e2e] ── Retry {attempt}/{OPENPNP_MAX_RETRIES} ──")
                    dubis_proc = _restart_dubis(dubis_proc, tmp_dir, test_source, base_url)
                    # Re-snapshot with fresh inventory
                    before = snapshot_quantities(base_url)
                    print(f"[e2e] Fresh snapshot: {len(before)} parts")
                    for lcsc_part in EXPECTED_DECREASES:
                        print(f"[e2e]   {lcsc_part}: qty={before.get(lcsc_part, 'NOT FOUND')}")

                print(f"[e2e] Launching OpenPnP: {openpnp_bin} (attempt {attempt}, display={xvfb_display})")
                openpnp_exit, openpnp_output, openpnp_log = _launch_and_wait_openpnp(
                    openpnp_bin, env, xvfb_display, TIMEOUT_SECONDS,
                )

                print(f"[e2e] OpenPnP exited with code {openpnp_exit}")
                print(f"[e2e] OpenPnP stdout ({len(openpnp_output)} bytes, last 3000 chars):\n{openpnp_output[-3000:]}")
                if openpnp_log:
                    print(f"[e2e] OpenPnP log ({len(openpnp_log)} bytes):\n{openpnp_log[-3000:]}")

                if openpnp_exit == 0:
                    break
                if attempt < OPENPNP_MAX_RETRIES:
                    print(f"[e2e] OpenPnP failed (code {openpnp_exit}), will retry...")

            if openpnp_exit != 0:
                failures.append(f"OpenPnP exited with code {openpnp_exit} after {OPENPNP_MAX_RETRIES} attempt(s)")

        # ── Verify ALL 6 placements decremented correctly ──
        after = snapshot_quantities(base_url)

        for lcsc_part, expected_decrease in EXPECTED_DECREASES.items():
            if lcsc_part not in before or lcsc_part not in after:
                failures.append(
                    f"{lcsc_part}: not found in inventory snapshot "
                    f"(before={lcsc_part in before}, after={lcsc_part in after})"
                )
                continue
            actual_decrease = before[lcsc_part] - after[lcsc_part]
            if actual_decrease != expected_decrease:
                failures.append(
                    f"{lcsc_part}: expected decrease of {expected_decrease}, "
                    f"got {actual_decrease} (before={before[lcsc_part]}, after={after[lcsc_part]})"
                )
            else:
                print(f"[e2e] PASS: {lcsc_part} decreased by {actual_decrease} (expected {expected_decrease})")

        # ── Test: GET /api/health still works ──
        status, body = _http_get(f"{base_url}/api/health")
        if status != 200 or not body or not body.get("ok"):
            failures.append("Health check failed after job run")
        else:
            print("[e2e] PASS: Health check OK after job")

        # ── Test: unknown part returns 404 ──
        status, body = _http_post(f"{base_url}/api/consume", {"part_id": "NONEXISTENT_XYZ"})
        if status != 404:
            failures.append(f"Unknown part should return 404, got {status}")
        else:
            print("[e2e] PASS: Unknown part returns 404")

        # ── Test: HTTP bridge endpoints (cross-compute only) ──
        # TODO: Deploy HttpBridge.py to test config to enable bridge tests.
        # The bridge is a production Jython script that isn't part of the test setup.
        if remote_openpnp:
            print("[e2e] INFO: Bridge tests skipped (HttpBridge.py not deployed to test config)")

        # ── Test: offline queue replay ──
        _test_offline_queue(base_url, dubis_proc, tmp_dir, test_source, failures)

        # ── Cleanup ──
        if local_backup_dir:
            _restore_local_openpnp(local_backup_dir)
            local_backup_dir = None  # Mark as handled
        if remote_openpnp and remote_backup_dir:
            from remote import cleanup
            cleanup(remote_openpnp, remote_backup_dir)
            remote_backup_dir = None

    finally:
        # Kill dubIS if still running
        if dubis_proc is not None and dubis_proc.poll() is None:
            dubis_proc.terminate()
            try:
                dubis_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                dubis_proc.kill()
        # OpenPnP is always waited-for inside _launch_and_wait_openpnp()
        # Kill window manager if it was started
        if wm_proc is not None and wm_proc.poll() is None:
            wm_proc.terminate()
        # Kill Xvfb if it was started
        if xvfb_proc is not None and xvfb_proc.poll() is None:
            xvfb_proc.terminate()
            try:
                xvfb_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xvfb_proc.kill()
        # Restore configs if not yet done
        if local_backup_dir:
            _restore_local_openpnp(local_backup_dir)
        if remote_openpnp and remote_backup_dir:
            try:
                from remote import cleanup
                cleanup(remote_openpnp, remote_backup_dir)
            except Exception:
                print(f"[e2e] WARN: Failed to restore remote OpenPnP config")
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Report ──
    if failures:
        print(f"\n[e2e] FAILED: {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    else:
        print("\n[e2e] ALL TESTS PASSED")
        return 0


def _test_bridge(remote_host, failures):
    """Test HTTP bridge endpoints on the remote OpenPnP host."""
    # Get the remote host's Tailscale IP from SSH config
    try:
        from remote import _load_ssh_config
        cfg = _load_ssh_config()
        host_cfg = cfg.lookup(remote_host)
        bridge_ip = host_cfg.get("hostname", remote_host)
    except Exception:
        print("[e2e] WARN: Could not determine remote bridge IP, skipping bridge test")
        return

    bridge_base = f"http://{bridge_ip}:8899"

    # Health
    status, body = _http_get(f"{bridge_base}/api/health")
    if status == 200 and body and body.get("ok"):
        print("[e2e] PASS: Bridge /api/health OK")
    else:
        failures.append(f"Bridge health check failed: status={status}")

    # Parts
    status, body = _http_get(f"{bridge_base}/api/parts")
    if status == 200 and body and body.get("ok"):
        num_parts = len(body.get("parts", []))
        if num_parts >= 3:
            print(f"[e2e] PASS: Bridge /api/parts returned {num_parts} parts")
        else:
            failures.append(f"Bridge /api/parts: expected >= 3 parts, got {num_parts}")
    else:
        failures.append(f"Bridge /api/parts failed: status={status}")

    # Feeders
    status, body = _http_get(f"{bridge_base}/api/feeders")
    if status == 200 and body and body.get("ok"):
        num_feeders = len(body.get("feeders", []))
        if num_feeders >= 3:
            print(f"[e2e] PASS: Bridge /api/feeders returned {num_feeders} feeders")
        else:
            failures.append(f"Bridge /api/feeders: expected >= 3 feeders, got {num_feeders}")
    else:
        failures.append(f"Bridge /api/feeders failed: status={status}")

    # State
    status, body = _http_get(f"{bridge_base}/api/state")
    if status == 200 and body and body.get("ok"):
        print("[e2e] PASS: Bridge /api/state OK")
    else:
        failures.append(f"Bridge /api/state failed: status={status}")


def _test_offline_queue(base_url, dubis_proc, tmp_dir, test_source, failures):
    """Test offline queue replay: stop dubIS, check queue, restart, verify flush."""
    # Stop dubIS
    dubis_proc.terminate()
    try:
        dubis_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        dubis_proc.kill()
        dubis_proc.wait()
    print("[e2e] dubIS stopped for offline queue test")

    # Check queue file
    queue_path = os.path.expanduser("~/.openpnp2/dubis_queue.json")
    queue_existed = os.path.exists(queue_path)
    queue_count = 0
    if queue_existed:
        with open(queue_path) as f:
            queue = json.load(f)
        queue_count = len(queue)
        print(f"[e2e] INFO: Queue file has {queue_count} entries")
    else:
        print("[e2e] INFO: No queue file (all events delivered successfully)")

    # Restart dubIS for remaining assertions
    dubis_cmd = [
        sys.executable, os.path.join(os.path.dirname(__file__), "dubis_headless.py"),
        "--data-dir", tmp_dir, "--port", str(DUBIS_PORT),
        "--test-source", test_source,
    ]
    dubis_proc_new = subprocess.Popen(
        dubis_cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    if wait_for_server(base_url, timeout=15):
        print("[e2e] PASS: dubIS restarted successfully after queue test")
        # Verify health
        status, body = _http_get(f"{base_url}/api/health")
        if status != 200 or not body or not body.get("ok"):
            failures.append("Health check failed after restart")
        # Verify queue was flushed after restart
        elif queue_existed and queue_count > 0:
            if os.path.exists(queue_path):
                with open(queue_path) as f:
                    remaining = json.load(f)
                if len(remaining) > 0:
                    failures.append(f"Queue not flushed after restart: {len(remaining)} entries remain")
                else:
                    print("[e2e] PASS: Queue flushed after restart (file empty)")
            else:
                print("[e2e] PASS: Queue file removed after restart (all events replayed)")
    else:
        failures.append("dubIS failed to restart after queue test")

    # Stop the restarted server
    dubis_proc_new.terminate()
    try:
        dubis_proc_new.wait(timeout=10)
    except subprocess.TimeoutExpired:
        dubis_proc_new.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PnP E2E test orchestrator")
    parser.add_argument("--remote-openpnp", default=None,
                        help="SSH host alias for remote OpenPnP (cross-compute mode)")
    args = parser.parse_args()
    sys.exit(run_test(remote_openpnp=args.remote_openpnp))
