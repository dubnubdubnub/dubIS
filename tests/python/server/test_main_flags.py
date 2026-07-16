"""Tests for server/__main__.py's --test-source / --rollback-on-exit flags.

Covers the semantics ported from tests/pnp-e2e/dubis_headless.py before that
file is deleted: a --test-source tag forces every adjustment made through
this server instance to carry the tag (overriding whatever source the caller
supplies, including PnP's hardcoded "openpnp"), so a single rollback_source
call cleans up everything a test session touched. --rollback-on-exit wires
that rollback into atexit; the shutdown hook itself is a plain function so
tests can simulate it without spinning up a real signal/atexit dance.
"""

from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

from server.__main__ import _build_api, _mount_test_routes, _rollback_on_exit, _tag_source
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger


@pytest.fixture
def api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


def _read_adjustment_sources(adjustments_csv):
    with open(adjustments_csv, newline="", encoding="utf-8-sig") as f:
        return [row["source"] for row in csv.DictReader(f)]


class TestBuildApiRepointing:
    def test_build_api_repoints_base_dir(self, tmp_path):
        data_dir = str(tmp_path / "data")
        import os
        os.makedirs(data_dir, exist_ok=True)
        api = _build_api(data_dir)
        assert api.base_dir == data_dir

    def test_build_api_repoints_distributor_credentials(self, tmp_path):
        """Regression: InventoryApi.__init__ constructs self._distributors
        bound to the default (real repo) base_dir before _build_api ever
        repoints api.base_dir — DistributorManager captures a plain string,
        not a live reference, so a naive base_dir reassignment doesn't
        follow. Without this fix, set_mouser_api_key (and any distributor
        credential op) silently reads/writes the REAL repo's data/ directory
        instead of --data-dir. This is exactly what polluted the real repo's
        data/mouser_credentials.json while developing this test — verify via
        the actual write, entirely inside tmp_path (no real-repo I/O)."""
        import os
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir, exist_ok=True)

        api = _build_api(data_dir)
        api.set_mouser_api_key("test-key-123")

        creds_path = os.path.join(data_dir, "mouser_credentials.json")
        assert os.path.exists(creds_path), (
            "mouser_credentials.json was not written under --data-dir — "
            "DistributorManager is still bound to the repo's real data/ dir"
        )
        assert api._distributors._mouser._credentials_file == creds_path


class TestTagSource:
    def test_adjust_part_gets_tagged_regardless_of_caller_source(self, api):
        """Even when the caller passes no source (the real JS behavior — the
        frontend never sends one), the wrapped adjust_part forces the tag."""
        _tag_source(api, "test:session-1")

        api.adjust_part("set", "C100000", 5, "note")

        assert _read_adjustment_sources(api.adjustments_csv) == ["test:session-1"]

    def test_adjust_part_override_wins_over_explicit_caller_source(self, api):
        """PnP's route hardcodes source="openpnp" — the tag must still win,
        since --test-source's job is to make EVERY adjustment cleanable."""
        _tag_source(api, "test:session-1")

        api.adjust_part("set", "C100000", 5, "note", source="openpnp")

        assert _read_adjustment_sources(api.adjustments_csv) == ["test:session-1"]

    def test_consume_bom_gets_tagged(self, api):
        _tag_source(api, "test:session-2")

        api.consume_bom(
            [{"part_key": "C100000", "bom_qty": 1}], 1, "board.csv", "note",
        )

        assert _read_adjustment_sources(api.adjustments_csv) == ["test:session-2"]

    def test_untagged_api_is_unaffected(self, api):
        """Sanity: without _tag_source, the caller's source (or empty) passes
        through unchanged — proves the wrapper is additive, not a global."""
        api.adjust_part("set", "C100000", 5, "note")
        assert _read_adjustment_sources(api.adjustments_csv) == [""]


class TestRollbackOnExit:
    def test_rollback_on_exit_removes_tagged_adjustments(self, api):
        """Simulates the atexit-registered shutdown hook directly (no real
        SIGTERM/atexit dance needed to test the semantics)."""
        _tag_source(api, "test:session-3")
        api.adjust_part("set", "C100000", 5, "note")
        api.adjust_part("add", "C100000", 2, "note2")
        assert _read_adjustment_sources(api.adjustments_csv) == [
            "test:session-3", "test:session-3",
        ]

        _rollback_on_exit(api, "test:session-3")

        assert _read_adjustment_sources(api.adjustments_csv) == []

    def test_rollback_on_exit_leaves_other_sources_alone(self, api):
        _tag_source(api, "test:session-4")
        api.adjust_part("set", "C100000", 5, "note")
        # A non-tagged adjustment made directly against the underlying api
        # (bypassing the wrapper) — e.g. a manual/import-style adjustment —
        # must survive rollback.
        from inventory_api import InventoryApi
        InventoryApi.adjust_part(api, "add", "C100000", 1, "manual note", source="manual")

        _rollback_on_exit(api, "test:session-4")

        assert _read_adjustment_sources(api.adjustments_csv) == ["manual"]


class TestMountTestRoutes:
    def test_reset_route_truncates_tagged_adjustments_and_rebuilds(self, api):
        _tag_source(api, "test:session-5")
        api.adjust_part("set", "C100000", 5, "note")

        app = create_app(api)
        _mount_test_routes(app, api, "test:session-5")

        with TestClient(app) as client:
            resp = client.post("/v1/_test/reset")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True

        assert _read_adjustment_sources(api.adjustments_csv) == []

    def test_reset_route_wins_over_static_catchall_mount(self, api, tmp_path):
        """Regression: server/app.py mounts StaticFiles at "/" (a catch-all
        Mount) when --static-dir is given — the same setup Playwright's live
        project uses. StaticFiles 405s any non-GET/HEAD method rather than
        falling through, so if the test route isn't ordered ahead of that
        mount, POST /v1/_test/reset 405s instead of resetting."""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<html></html>")

        _tag_source(api, "test:session-6")
        api.adjust_part("set", "C100000", 5, "note")

        app = create_app(api, static_dir=str(static_dir))
        _mount_test_routes(app, api, "test:session-6")

        with TestClient(app) as client:
            resp = client.post("/v1/_test/reset")
            assert resp.status_code == 200, resp.text
            assert resp.json()["ok"] is True

        assert _read_adjustment_sources(api.adjustments_csv) == []

    def test_reset_route_not_mounted_without_test_source(self, api):
        """The route must NOT exist on server/app.py's production surface —
        only server/__main__.py's test-flagged wiring adds it."""
        app = create_app(api)
        with TestClient(app) as client:
            resp = client.post("/v1/_test/reset")
        assert resp.status_code == 404
