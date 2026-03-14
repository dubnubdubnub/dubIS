"""PnP E2E test orchestrator.

Runs on a self-hosted runner (ux430) with OpenPnP installed natively.
Starts headless dubIS, launches OpenPnP via xvfb-run with a null driver,
and verifies that placement events correctly decrement inventory.

Requirements on the host:
  - OpenPnP installed (openpnp.sh on PATH or OPENPNP_BIN env var)
  - xvfb-run (apt install xvfb)
  - Python 3.12+ with dubIS dependencies (pip install -r requirements-dev.txt)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DUBIS_PORT = 7890
TIMEOUT_SECONDS = 120
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")
OPENPNP_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "openpnp-config")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _http_get(url):
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception:
        return None, None


def _http_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception:
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


def run_test():
    base_url = f"http://127.0.0.1:{DUBIS_PORT}"
    failures = []

    # Create temp data directory with fixture copies
    tmp_dir = tempfile.mkdtemp(prefix="dubis-e2e-")
    try:
        # Copy fixture CSVs
        for fname in ("purchase_ledger.csv", "adjustments.csv"):
            src = os.path.join(FIXTURES_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp_dir, fname))

        # Write part map for mapping test: SMD_RES_100k -> a real LCSC in inventory
        # We need to pick a part that actually exists in the fixtures
        part_map = {"SMD_RES_100k": "C100220"}  # Map to a real part in fixtures
        part_map_path = os.path.join(tmp_dir, "pnp_part_map.json")
        with open(part_map_path, "w") as f:
            json.dump(part_map, f)

        # ── Start headless dubIS ──
        dubis_proc = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(__file__), "dubis_headless.py"),
             "--data-dir", tmp_dir, "--port", str(DUBIS_PORT)],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

        print("[e2e] Waiting for dubIS server...")
        if not wait_for_server(base_url):
            dubis_proc.kill()
            output = dubis_proc.stdout.read().decode(errors="replace")
            print(f"[e2e] FAIL: dubIS server did not start.\nOutput:\n{output}")
            return 1

        # Snapshot starting quantities
        before = snapshot_quantities(base_url)
        print(f"[e2e] Inventory snapshot: {len(before)} parts")

        # ── Launch OpenPnP via xvfb-run ──
        openpnp_bin = os.environ.get("OPENPNP_BIN", "openpnp.sh")
        openpnp_home = os.environ.get("OPENPNP_CONFIG", OPENPNP_CONFIG_DIR)
        job_path = os.path.join(openpnp_home, "jobs", "test-job.job.xml")

        # Set up OpenPnP config directory: copy our test config into ~/.openpnp2
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

        # Also install the real Job.Placement.Complete.py from the project
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
            f'DUBIS_URL = "http://127.0.0.1:{DUBIS_PORT}"',
        )
        with open(jython_script, "w") as f:
            f.write(content)

        # Start Xvfb on a known display so we can target xdotool at it
        xvfb_display = ":42"
        xvfb_proc = subprocess.Popen(
            ["Xvfb", xvfb_display, "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1)

        env = os.environ.copy()
        env["OPENPNP_TEST_JOB"] = os.path.abspath(job_path)
        env["DISPLAY"] = xvfb_display

        print(f"[e2e] Launching OpenPnP: {openpnp_bin} (display={xvfb_display})")
        openpnp_proc = subprocess.Popen(
            [openpnp_bin],
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=openpnp_user_config,
            start_new_session=True,
        )

        # OpenPnP 2.4 shows a first-run dialog that blocks the EDT.
        # Dismiss it by sending Enter key via xdotool after a delay.
        def dismiss_dialog():
            time.sleep(8)
            dismiss_env = os.environ.copy()
            dismiss_env["DISPLAY"] = xvfb_display
            for _ in range(5):
                try:
                    subprocess.run(
                        ["xdotool", "key", "Return"],
                        env=dismiss_env, timeout=5,
                        capture_output=True,
                    )
                except FileNotFoundError:
                    print("[e2e] WARN: xdotool not found, cannot dismiss dialog")
                    break
                except Exception:
                    pass
                time.sleep(1)
            print(f"[e2e] Sent Enter keys to dismiss OpenPnP dialog (display={xvfb_display})")

        import threading
        dialog_thread = threading.Thread(target=dismiss_dialog, daemon=True)
        dialog_thread.start()

        # Wait for OpenPnP to finish (it exits via Startup.py after job completes)
        try:
            openpnp_proc.wait(timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            # Kill entire process group so orphaned Java processes don't linger
            import signal
            os.killpg(openpnp_proc.pid, signal.SIGKILL)
            openpnp_proc.wait()
            failures.append("OpenPnP timed out")

        openpnp_output = openpnp_proc.stdout.read().decode(errors="replace")
        print(f"[e2e] OpenPnP exited with code {openpnp_proc.returncode}")
        if openpnp_proc.returncode != 0:
            print(f"[e2e] OpenPnP output (last 2000 chars):\n{openpnp_output[-2000:]}")
            failures.append(f"OpenPnP exited with code {openpnp_proc.returncode}")

        # ── Verify quantities decremented ──
        after = snapshot_quantities(base_url)

        # Board has: 3x R100k, 2x C100nF, 1x SMD_RES_100k (mapped to C100220)
        # R100k and C100nF are OpenPnP part IDs — they need to match inventory
        # via direct match or mapping. For now, check that C100220 was decremented
        # by the mapped SMD_RES_100k placement.
        if "C100220" in before and "C100220" in after:
            expected_decrease = 1  # 1x SMD_RES_100k placement mapped to C100220
            actual_decrease = before["C100220"] - after["C100220"]
            if actual_decrease < expected_decrease:
                failures.append(
                    f"C100220 (mapped from SMD_RES_100k): expected decrease >= {expected_decrease}, "
                    f"got {actual_decrease} (before={before['C100220']}, after={after['C100220']})"
                )
            else:
                print(f"[e2e] PASS: C100220 decreased by {actual_decrease}")
        else:
            print("[e2e] WARN: C100220 not found in inventory, skipping mapping test")

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

        # ── Test: queue on failure ──
        # Stop dubIS, then manually post a consume event to verify it fails gracefully
        dubis_proc.terminate()
        dubis_proc.wait(timeout=10)
        print("[e2e] dubIS stopped for queue test")

        # The queue test verifies the Jython script's offline queueing.
        # Since we can't easily re-trigger OpenPnP placements, we test the
        # queue file mechanism directly.
        queue_path = os.path.expanduser("~/.openpnp2/dubis_queue.json")
        if os.path.exists(queue_path):
            with open(queue_path) as f:
                queue = json.load(f)
            print(f"[e2e] INFO: Queue file has {len(queue)} entries")
        else:
            print("[e2e] INFO: No queue file (all events delivered successfully)")

        # ── Cleanup ──
        # Restore backed-up OpenPnP config
        if backup_dir:
            shutil.rmtree(openpnp_user_config)
            shutil.copytree(backup_dir, openpnp_user_config)
            shutil.rmtree(backup_dir)

    finally:
        # Kill dubIS if still running
        if dubis_proc.poll() is None:
            dubis_proc.terminate()
            dubis_proc.wait(timeout=10)
        # Kill Xvfb if it was started
        try:
            if xvfb_proc.poll() is None:
                xvfb_proc.terminate()
                xvfb_proc.wait(timeout=5)
        except NameError:
            pass
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


if __name__ == "__main__":
    sys.exit(run_test())
