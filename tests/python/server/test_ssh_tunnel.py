"""Hub-managed `ssh://` tunnels (server/ssh_tunnel.py).

The URL grammar is pinned against the SAME case table the JS half reads
(tests/fixtures/ssh/url-cases.json), so the roster's two validators cannot
drift. The supervisor is exercised against a real subprocess —
tests/fixtures/ssh/fake_ssh.py, handed the exact argv the hub builds — rather
than a mocked Popen, because the interesting failures (a process that exits,
stderr arriving late, a port that only accepts after "auth") are process
behavior.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from dubis_errors import SourceConfigError
from server import sources as sources_mod
from server import ssh_tunnel
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

REPO = Path(__file__).resolve().parents[3]
FAKE_SSH = str(REPO / "tests" / "fixtures" / "ssh" / "fake_ssh.py")
CASES = json.loads((REPO / "tests" / "fixtures" / "ssh" / "url-cases.json").read_text())
SSH_URL = "ssh://isaac@box.example/127.0.0.1:7891"
SOCK_URL = "ssh://isaac@box.example/run/dubis/dubis.sock"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Configure fake_ssh for this test; returns a helper to read its spawn log."""
    log = tmp_path / "ssh-log.jsonl"
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")

    class Fake:
        def mode(self, mode: str, **env: str) -> None:
            monkeypatch.setenv("FAKE_SSH_MODE", mode)
            for key, value in env.items():
                monkeypatch.setenv(key, value)

        def spawns(self) -> list[list[str]]:
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines() if line]

    return Fake()


@pytest.fixture
def manager(tmp_path):
    mgr = ssh_tunnel.TunnelManager(ssh_command=[sys.executable, FAKE_SSH],
                                   state_dir=str(tmp_path), start_timeout=10.0)
    yield mgr
    mgr.close_all()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie still answers kill(0); ask ps whether it is really running.
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True, check=False).stdout.strip()
    return bool(out) and not out.startswith("Z")


# ── grammar ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES["valid"], ids=lambda c: c["url"])
def test_valid_urls_parse_to_the_shared_canonical_form(case):
    target = ssh_tunnel.parse_ssh_url(case["url"])
    assert target.canonical == case["canonical"]
    assert target.host == case["host"]
    assert target.user == case["user"]
    assert target.port == case["port"]
    assert target.remote == case["remote"]
    assert bool(target.remote_socket) == (case["kind"] == "socket")
    # Canonical is a fixed point: the stored form re-parses to itself.
    assert ssh_tunnel.parse_ssh_url(target.canonical) == target
    assert sources_mod.normalize_url(case["url"]) == case["canonical"]


@pytest.mark.parametrize("case", CASES["invalid"], ids=lambda c: c["url"])
def test_invalid_urls_are_refused_with_a_reason(case):
    with pytest.raises(ssh_tunnel.SshUrlError) as info:
        ssh_tunnel.parse_ssh_url(case["url"])
    assert case["reason"].lower() in str(info.value).lower()
    assert sources_mod.normalize_url(case["url"]) == ""
    with pytest.raises(SourceConfigError, match="ssh://"):
        sources_mod.require_url(case["url"])


def test_display_name_of_an_ssh_source_is_the_ssh_host():
    assert sources_mod.name_from_url(SOCK_URL) == "box.example"


def test_argv_is_batch_mode_with_the_documented_options():
    tunnel = ssh_tunnel.SshTunnel(ssh_tunnel.parse_ssh_url("ssh://me@box:2222/run/d.sock"),
                                  ssh_command=["ssh"])
    argv = tunnel._argv(40001)
    joined = " ".join(argv)
    for opt in ("BatchMode=yes", "ExitOnForwardFailure=yes", "ServerAliveInterval=15",
                "ServerAliveCountMax=3", "ControlPath=none"):
        assert opt in joined
    assert argv[0] == "ssh" and "-N" in argv
    assert argv[argv.index("-L") + 1] == "127.0.0.1:40001:/run/d.sock"
    assert argv[argv.index("-p") + 1] == "2222"
    # `--` before the destination: nothing in the URL can become an option.
    assert argv[-2:] == ["--", "me@box"]


# ── lifecycle ───────────────────────────────────────────────────────────────


def _get(tunnel: ssh_tunnel.SshTunnel, path: str) -> httpx.Response:
    async def go() -> httpx.Response:
        async with httpx.AsyncClient(base_url=ssh_tunnel.PLACEHOLDER_BASE_URL,
                                     transport=ssh_tunnel.TunnelTransport(tunnel)) as client:
            return await client.get(path)
    return asyncio.run(go())


def test_lazy_start_then_requests_flow_through_the_tunnel(fake, manager):
    tunnel = manager.get(SSH_URL)
    assert fake.spawns() == []  # nothing spawned until first use
    assert tunnel.status()["state"] == ssh_tunnel.STATE_IDLE

    response = _get(tunnel, "/v1/parts")
    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "127.0.0.1:7891"
    assert body["dest"] == "isaac@box.example"
    assert body["host_header"] == f"127.0.0.1:{tunnel.local_port}"
    status = tunnel.status()
    assert status["state"] == ssh_tunnel.STATE_UP
    assert status["local_url"] == f"http://127.0.0.1:{tunnel.local_port}"

    # A second request reuses the running process.
    _get(tunnel, "/v1/meta")
    assert len(fake.spawns()) == 1


def test_an_exited_tunnel_is_restarted_on_next_use_on_the_same_port(fake, manager):
    fake.mode("die", FAKE_SSH_LIFETIME="0.4")
    tunnel = manager.get(SSH_URL)
    _get(tunnel, "/v1/health")
    first_port, first_pid = tunnel.local_port, tunnel.pid
    time.sleep(1.0)  # the fake exits 255 with "Timeout, server not responding."

    status = tunnel.status()
    assert status["state"] == ssh_tunnel.STATE_FAILED
    assert "server not responding" in status["error"]

    fake.mode("ok")
    response = _get(tunnel, "/v1/health")
    assert response.json() == {"ok": True}
    assert tunnel.local_port == first_port
    assert tunnel.pid != first_pid
    assert tunnel.status()["restarts"] == 1
    assert len(fake.spawns()) == 2


def test_a_tunnel_outlives_the_thread_that_started_it(fake, manager):
    """PR_SET_PDEATHSIG (Linux) fires when the forking THREAD exits. ensure()
    runs on short-lived anyio worker threads, so without the dedicated spawner
    thread this tunnel would be SIGTERMed as soon as its starter went away."""
    import threading

    tunnel = manager.get(SSH_URL)
    starter = threading.Thread(target=tunnel.ensure)
    starter.start()
    starter.join()
    time.sleep(0.5)
    assert tunnel.pid is not None and _alive(tunnel.pid)
    assert tunnel.status()["state"] == ssh_tunnel.STATE_UP


def test_auth_failure_names_the_fix_and_backs_off(fake, manager):
    fake.mode("denied")
    tunnel = manager.get(SSH_URL)
    with pytest.raises(ssh_tunnel.TunnelError) as info:
        tunnel.ensure()
    assert info.value.kind == ssh_tunnel.KIND_AUTH
    message = str(info.value)
    assert "Permission denied" in message
    assert "ssh-agent" in message and "authorized_keys" in message

    # Inside the backoff window the last failure is re-raised without spawning.
    with pytest.raises(ssh_tunnel.TunnelError):
        tunnel.ensure()
    assert len(fake.spawns()) == 1
    status = tunnel.status()
    assert status["state"] == ssh_tunnel.STATE_FAILED and status["kind"] == "auth"


def test_dns_failure_is_classified(fake, manager):
    fake.mode("dns")
    with pytest.raises(ssh_tunnel.TunnelError) as info:
        manager.get(SSH_URL).ensure()
    assert info.value.kind == ssh_tunnel.KIND_DNS
    assert "cannot resolve box.example" in str(info.value)


def test_a_start_that_never_comes_up_times_out_and_is_killed(fake, tmp_path):
    fake.mode("hang")
    mgr = ssh_tunnel.TunnelManager(ssh_command=[sys.executable, FAKE_SSH], start_timeout=0.8)
    tunnel = mgr.get(SSH_URL)
    with pytest.raises(ssh_tunnel.TunnelError) as info:
        tunnel.ensure()
    assert info.value.kind == ssh_tunnel.KIND_TIMEOUT
    assert tunnel.pid is None
    mgr.close_all()


def test_a_dead_remote_target_is_reported_as_such(fake, manager):
    fake.mode("open_failed")
    tunnel = manager.get(SOCK_URL)
    with pytest.raises(ssh_tunnel.TunnelError) as info:
        _get(tunnel, "/v1/health")
    assert info.value.kind == ssh_tunnel.KIND_REMOTE_TARGET
    assert "/run/dubis/dubis.sock" in str(info.value)
    # ssh itself is fine, and the status says so — while naming the problem.
    status = tunnel.status()
    assert status["state"] == ssh_tunnel.STATE_UP
    assert status["kind"] == ssh_tunnel.KIND_REMOTE_TARGET


def test_no_ssh_binary_is_a_typed_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _name: None)
    assert ssh_tunnel.available() is False
    tunnel = ssh_tunnel.TunnelManager().get(SSH_URL)
    with pytest.raises(ssh_tunnel.TunnelError) as info:
        tunnel.ensure()
    assert info.value.kind == ssh_tunnel.KIND_NO_SSH
    assert "OpenSSH" in str(info.value)


def test_close_all_terminates_every_tunnel_and_clears_the_pidfile(fake, manager, tmp_path):
    tunnel = manager.get(SSH_URL)
    tunnel.ensure()
    pid = tunnel.pid
    pidfile = tmp_path / ssh_tunnel.PIDFILE_NAME
    assert json.loads(pidfile.read_text())[0]["pid"] == pid
    manager.close_all()
    assert not _alive(pid)
    assert not pidfile.exists()
    # A closed manager refuses to start anything new.
    with pytest.raises(ssh_tunnel.TunnelError):
        tunnel.ensure()


def test_reap_kills_a_tunnel_a_previous_hub_left_behind(fake, tmp_path):
    fake.mode("hang")
    forward = "127.0.0.1:1:/run/dubis/dubis.sock"
    orphan = subprocess.Popen([sys.executable, FAKE_SSH, "-N", "-L", forward, "--", "me@box"])
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        time.sleep(0.3)
        (tmp_path / ssh_tunnel.PIDFILE_NAME).write_text(json.dumps([
            {"pid": orphan.pid, "forward": forward},
            # A recorded pid whose command no longer matches (pid reuse) is spared.
            {"pid": unrelated.pid, "forward": "127.0.0.1:2:/x"},
        ]))
        # The fake is `python fake_ssh.py`, so its command line contains "ssh".
        killed = ssh_tunnel.reap_stale(str(tmp_path))
        assert killed == [orphan.pid]
        orphan.wait(timeout=5)
        assert unrelated.poll() is None
        assert not (tmp_path / ssh_tunnel.PIDFILE_NAME).exists()
    finally:
        for proc in (orphan, unrelated):
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
                proc.wait()


# ── through the hub ─────────────────────────────────────────────────────────


@pytest.fixture
def hub(tmp_path, fake):
    api = make_api(tmp_path)
    write_ledger(api, [make_part(lcsc="C100000", qty=10)])
    app = create_app(api)
    tunnels = ssh_tunnel.TunnelManager(ssh_command=[sys.executable, FAKE_SSH],
                                       start_timeout=10.0)
    app.state.source_clients = sources_mod.SourceClients(tunnels=tunnels)
    with TestClient(app) as client:
        client.tunnels = tunnels
        yield client
    api.shutdown()
    tunnels.close_all()


def _add(hub, url: str) -> dict:
    response = hub.post("/v1/sources", json={"url": url, "name": "Box"})
    assert response.status_code == 200, response.text
    return response.json()["detail"]["source"]


def test_hub_reports_an_up_tunnel_and_proxies_through_it(hub):
    source = _add(hub, SSH_URL + "/")  # stored canonical, trailing slash gone
    assert source["url"] == SSH_URL
    assert source["id"] == "box-example"

    listing = hub.get("/v1/sources").json()["sources"]
    entry = next(s for s in listing if s["id"] == source["id"])
    assert entry["reachable"] is True
    assert entry["auth"] == "ok"
    assert entry["tunnel"]["state"] == "up"
    assert entry["tunnel"]["local_url"].startswith("http://127.0.0.1:")

    proxied = hub.get("/v1/carts", headers={"X-Dubis-Source": source["id"]})
    assert proxied.status_code == 200
    assert proxied.json()["target"] == "127.0.0.1:7891"


def test_hub_reports_why_a_tunnel_failed(hub, fake):
    fake.mode("denied")
    source = _add(hub, SSH_URL)
    entry = next(s for s in hub.get("/v1/sources").json()["sources"] if s["id"] == source["id"])
    assert entry["reachable"] is False
    # The detail is the sentence, not the exception class — this is what the
    # dot's tooltip and the tab strip show.
    assert "Permission denied" in entry["detail"]
    assert entry["auth"] == "unknown"
    assert entry["tunnel"]["state"] == "failed"
    assert entry["tunnel"]["kind"] == "auth"

    proxied = hub.get("/v1/carts", headers={"X-Dubis-Source": source["id"]})
    assert proxied.status_code == 502
    assert "Permission denied" in proxied.text


def test_http_sources_carry_no_tunnel_field(hub):
    hub.post("/v1/sources", json={"url": "http://bench.local:7891"})
    entry = hub.get("/v1/sources").json()["sources"][0]
    assert entry["tunnel"] is None


def test_an_invalid_ssh_url_is_a_400_with_the_grammar_reason(hub):
    response = hub.post("/v1/sources", json={"url": "ssh://box"})
    assert response.status_code == 400
    assert "remote target" in response.text
